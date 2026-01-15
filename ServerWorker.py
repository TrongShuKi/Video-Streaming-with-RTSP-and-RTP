from random import randint
import sys, traceback, threading, socket
from VideoStream import VideoStream
from RtpPacket import RtpPacket

class ServerWorker:
    SETUP = 'SETUP'
    PLAY = 'PLAY'
    PAUSE = 'PAUSE'
    TEARDOWN = 'TEARDOWN'
    
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    OK_200 = 0
    FILE_NOT_FOUND_404 = 1
    CON_ERR_500 = 2
    
    clientInfo = {}
    
    def __init__(self, clientInfo):
        self.clientInfo = clientInfo
        self.rtpSequenceNumber = 0 # đếm tổng gói tin
        
    def run(self):
        threading.Thread(target=self.recvRtspRequest).start()
    
    def recvRtspRequest(self):
        """Receive RTSP request from the client."""
        connSocket = self.clientInfo['rtspSocket'][0]
        while True:            
            try:
                data = connSocket.recv(256)
                if data:
                    print("Data received:\n" + data.decode("utf-8"))
                    self.processRtspRequest(data.decode("utf-8"))
            except:
                break
    
    def processRtspRequest(self, data):
        """Process RTSP request sent from the client."""
        try:
            request = data.split('\n')
            line1 = request[0].split(' ')
            requestType = line1[0]
            filename = line1[1]
            seq = request[1].split(' ')
        except:
            return
        
        if requestType == self.SETUP:
            if self.state == self.INIT:
                print("processing SETUP\n")
                try:
                    self.clientInfo['videoStream'] = VideoStream(filename)
                    self.state = self.READY
                except IOError:
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
                
                self.clientInfo['session'] = randint(100000, 999999)
                
                # Lấy tổng số frame gửi đến CLient để tính time và thanh tiến trình
                total_frames = self.clientInfo['videoStream'].totalFrames()
                self.replyRtsp(self.OK_200, seq[1], total_frames)
                
                self.clientInfo['rtpPort'] = request[2].split(' ')[3]
        
        elif requestType == self.PLAY:
            if self.state == self.READY:
                print("processing PLAY\n")
                self.state = self.PLAYING
                
                # Xử lý lệnh Tua
                for line in request:
                    if "Frame:" in line:
                        try:
                            seek_frame = int(line.split(' ')[1])
                            self.clientInfo['videoStream'].seek(seek_frame)
                            print(f"Seeking to frame: {seek_frame}")
                        except: pass

                self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.replyRtsp(self.OK_200, seq[1])
                self.clientInfo['event'] = threading.Event()
                self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
                self.clientInfo['worker'].start()
        
        elif requestType == self.PAUSE:
            if self.state == self.PLAYING:
                print("processing PAUSE\n")
                self.state = self.READY
                self.clientInfo['event'].set()
                self.replyRtsp(self.OK_200, seq[1])
        
        elif requestType == self.TEARDOWN:
            print("processing TEARDOWN\n")
            self.clientInfo['event'].set()
            self.replyRtsp(self.OK_200, seq[1])
            self.clientInfo['rtpSocket'].close()
            
    def sendRtp(self):
        """Send RTP packets over UDP."""
        MAX_PAYLOAD_SIZE = 1400 
        while True:
            # Speed send packet 0.05 = 20 fps, 0.033 = 30fps
            self.clientInfo['event'].wait(0.05) 
            # Kiểm tra xem người dùng có bấm PAUSE hoặc TEARDOWN không. Nếu có, lệnh break sẽ thoát khỏi vòng lặp và ngừng gửi dữ liệu.
            if self.clientInfo['event'].isSet(): break 
                
            data = self.clientInfo['videoStream'].nextFrame()
            if data: 
                frameNumber = self.clientInfo['videoStream'].frameNbr()
                data_length = len(data)
                address = self.clientInfo['rtspSocket'][1][0]
                port = int(self.clientInfo['rtpPort'])
                # Dùng frameNumber làm Timestamp để gom nhóm
                currentTimestamp = frameNumber
                try:
                    # Nếu <= 1400 gửi trực tiếp
                    if data_length <= MAX_PAYLOAD_SIZE: 
                        self.rtpSequenceNumber += 1
                        self.clientInfo['rtpSocket'].sendto(
                            self.makeRtp(data, self.rtpSequenceNumber, 1, currentTimestamp), (address, port))
                    # Nếu > 1400 (HD) phân mảnh rồi mới gửi
                    else: 
                        curr_pos = 0
                        while curr_pos < data_length:
                            chunk = data[curr_pos : curr_pos + MAX_PAYLOAD_SIZE]
                            curr_pos += MAX_PAYLOAD_SIZE
                            # Hết dữ liệu, Marker = 1 (Gói cuối) <> Còn dữ liệu, Marker = 0 (Gói giữa)
                            marker = 1 if curr_pos >= data_length else 0

                            self.rtpSequenceNumber += 1
                            self.clientInfo['rtpSocket'].sendto(
                                self.makeRtp(chunk, self.rtpSequenceNumber, marker, currentTimestamp), (address, port))
                except:
                    print("Connection Error")

    def makeRtp(self, payload, seqnum, marker, timestamp):
        version = 2
        padding = 0
        extension = 0
        cc = 0
        pt = 26 
        ssrc = 0 
        rtpPacket = RtpPacket()
        rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload, timestamp)
        return rtpPacket.getPacket()
        
    def replyRtsp(self, code, seq, total_frames=None):
        if code == self.OK_200:
            reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
            # Thêm tham số total_frames vào reply để gửi qua Client.
            if total_frames:
                reply += '\nTotalFrames: ' + str(total_frames)
            
            connSocket = self.clientInfo['rtspSocket'][0]
            connSocket.send(reply.encode())
        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")