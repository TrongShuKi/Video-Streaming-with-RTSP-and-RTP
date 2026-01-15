from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
from RtpPacket import RtpPacket
import queue, time
import io 

class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT
    
    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    FPS = 25.0               
    TOTAL_NO_FRAMES = 1 
    
    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.connectToServer()
        self.frameNbr = 0
        self.highestFrameNbr = 0
        
        # --- CẤU HÌNH BUFFERING ---
        self.MAX_BUFFER_SIZE = 150 
        self.frameBuffer = queue.Queue(maxsize=self.MAX_BUFFER_SIZE) 
        self.BUFFER_THRESHOLD = 50 
        self.isBuffering = True    
        self.isSeeking = False     
        self.play_loop_active = False 
        
        # --- UI PAUSE ---
        self.auto_pause_sent = False  # Server bị dừng do buffer đầy
        self.is_ui_paused = False     # Người dùng bấm Pause (chỉ dừng hình, vẫn tải ngầm)

        # --- CẤU HÌNH GIAO DIỆN CỐ ĐỊNH (THÊM VÀO ĐÂY) ---
        self.GUI_WIDTH = 640  
        self.GUI_HEIGHT = 360

    def handler(self):
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()
        
    def createWidgets(self):
        WIDTH = 20
        self.setup = Button(self.master, width=WIDTH, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self.setup["command"] = self.setupMovie
        self.setup.grid(row=3, column=0, padx=2, pady=2)
        
        self.start = Button(self.master, width=WIDTH, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=3, column=1, padx=2, pady=2)
        
        self.pause = Button(self.master, width=WIDTH, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=3, column=2, padx=2, pady=2)
        
        self.teardown = Button(self.master, width=WIDTH, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] =  self.exitClient
        self.teardown.grid(row=3, column=3, padx=2, pady=2)
        
        self.label = Label(self.master, height=24)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5) 

        # Hiển thị thời gian
        self.timeLabel = Label(self.master, text="00:00 / 00:00")
        self.timeLabel.grid(row=1, column=0, columnspan=4, sticky=W+E, padx=1)
        # Vẽ thanh tiến trình
        self.progress_width = 400
        self.progress_height = 8
        self.progress = Canvas(self.master, width=self.progress_width, height=self.progress_height, bg="#444444", highlightthickness=0)
        self.progress.grid(row=2, column=0, columnspan=4, sticky=W+E, padx=5, pady=5)
        self.buffer_rect = self.progress.create_rectangle(0, 0, 0, self.progress_height, fill="#888888", width=0, tags="buffer")
        self.play_rect = self.progress.create_rectangle(0, 0, 0, self.progress_height, fill="#FF0000", width=0, tags="play")
        self.progress.bind("<Button-1>", self.on_seek)
        
    def set_progress(self, current_frame):
        """Hiển thị tiến trình chính"""
        if self.TOTAL_NO_FRAMES > 0:
            ratio = current_frame / self.TOTAL_NO_FRAMES
            if ratio > 1: ratio = 1
            current_canvas_width = self.progress.winfo_width()
            if current_canvas_width < 2: current_canvas_width = self.progress_width
            new_width = current_canvas_width * ratio
            self.progress.coords(self.play_rect, 0, 0, new_width, self.progress_height)

    def set_buffer(self, buffer_frame):
        """Hiển thị tiến trình buffer"""
        if self.TOTAL_NO_FRAMES > 0:
            ratio = buffer_frame / self.TOTAL_NO_FRAMES
            if ratio > 1: ratio = 1
            current_canvas_width = self.progress.winfo_width()
            if current_canvas_width < 2: current_canvas_width = self.progress_width
            new_width = current_canvas_width * ratio
            self.progress.coords(self.buffer_rect, 0, 0, new_width, self.progress_height)

    def on_seek(self, event):
        if self.TOTAL_NO_FRAMES > 0:
            width = self.progress.winfo_width()
            ratio = event.x / width
            target_frame = int(self.TOTAL_NO_FRAMES * ratio)
            
            # TRƯỜNG HỢP 1: Tua tới trong phạm vi Buffer
            if self.frameNbr < target_frame < self.highestFrameNbr:
                print(f"Seeking to frame {target_frame}...")
                
                found_in_buffer = False
                with self.frameBuffer.mutex:
                    # Lặp và vứt bỏ các frame cũ (nhỏ hơn target_frame)
                    while len(self.frameBuffer.queue) > 0:
                        # Xem phần tử đầu tiên (không lấy ra)
                        first_item = self.frameBuffer.queue[0]
                        first_frame_num = first_item[0]
                        
                        if first_frame_num < target_frame:
                            # Frame cũ -> Vứt đi
                            self.frameBuffer.queue.popleft()
                        else:
                            # Đã gặp frame đích (hoặc lớn hơn) -> Dừng lại
                            found_in_buffer = True
                            # Cập nhật thông số để UI biết phát tiếp
                            self.frameNbr = first_frame_num
                            break
                
                if found_in_buffer:
                    print(f"Seek Success! Playing from frame {self.frameNbr}")
                    self.set_progress(self.frameNbr)
                    self.isBuffering = False
                    self.isSeeking = False
                    return # KẾT THÚC, KHÔNG GỌI SERVER

            # TRƯỜNG HỢP 2: Tua NGƯỢC hoặc Tua ra NGOÀI Buffer (Gọi Server)
            print(f"Seeking OUTSIDE/BACKWARDS to {target_frame}")
            
            # Cập nhật UI
            self.frameNbr = target_frame
            self.set_progress(target_frame)
            self.highestFrameNbr = target_frame 
            self.seekTarget = target_frame # Lưu lại mục tiêu để lọc gói tin
            
            # Xóa sạch Buffer cũ
            self.currBuffer = bytearray()
            with self.frameBuffer.mutex:
                self.frameBuffer.queue.clear()
            
            # Thiết lập trạng thái
            self.isBuffering = True
            self.isSeeking = True 
            self.auto_pause_sent = False 
            self.is_ui_paused = False 

            # Đảm bảo Thread nhận tin luôn sống
            if self.state == self.READY or self.is_ui_paused:
                self.playEvent = threading.Event()
                self.playEvent.clear()
                if not hasattr(self, 'rtp_thread') or not self.rtp_thread.is_alive():
                     self.rtp_thread = threading.Thread(target=self.listenRtp)
                     self.rtp_thread.start()
                self.start_play_loop()

            # Gửi lệnh PLAY kèm vị trí mới
            self.sendRtspRequest(self.PLAY, seekFrame=target_frame)

    def setupMovie(self):
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)
    
    def exitClient(self):
        self.sendRtspRequest(self.TEARDOWN)        
        self.master.destroy()

    def pauseMovie(self):
        """Pause UI nhưng vẫn tải ngầm"""
        if self.state == self.PLAYING:
            self.is_ui_paused = True
        
    def playMovie(self):
        """Resume UI hoặc Resume Server"""
        #Luôn kiểm tra xem thread nhận RTP có đang sống không. Nếu chết thì dựng dậy.
        if not hasattr(self, 'rtp_thread') or not self.rtp_thread.is_alive():
             if self.state != self.INIT: # Chỉ start nếu đã Setup
                self.rtp_thread = threading.Thread(target=self.listenRtp)
                self.rtp_thread.start()

        # Trường hợp 1: Chỉ là đang Pause UI, server vẫn đang chạy hoặc đã tự ngắt
        if self.is_ui_paused:
            self.is_ui_paused = False
            return 

        # Trường hợp 2: Server chưa chạy (lần đầu hoặc sau khi Teardown/Pause thật)
        self.auto_pause_sent = False 
        if self.state == self.READY:
            self.playEvent = threading.Event()
            self.playEvent.clear()
            
            if self.requestSent == self.PLAY:
                self.state = self.PLAYING
                self.master.after(0, self.start_play_loop)
            else:
                self.rtp_thread = threading.Thread(target=self.listenRtp)
                self.rtp_thread.start()
                self.sendRtspRequest(self.PLAY)

    def listenRtp(self):        
        self.currBuffer = [] 
        self.currentTimestamp = -1 # Dùng Timestamp = Frame hiện tại để theo dõi frame
        
        while True:
            try:
                # Nhận dữ liệu từ mạng với kích thước tối đa của UDP
                data = self.rtpSocket.recv(65535)
                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data) # Tách Header
                    
                    currSeq = rtpPacket.seqNum() # Thuộc frame nào
                    marker = rtpPacket.getMarker() # Để biết hêt frame chưa
                    payload = rtpPacket.getPayload() # Lấy ảnh/ 1 phần ảnh
                    currTimestamp = rtpPacket.timestamp() # = FrameNumber

                    # Nếu Timestamp khác gói trước -> Đây là Frame mới
                    if currTimestamp != self.currentTimestamp:
                        self.currentTimestamp = currTimestamp
                        self.currBuffer = []
                    
                    # Lưu mảnh vào buffer tạm kèm SeqNum để sắp xếp
                    self.currBuffer.append((currSeq, payload))
                    
                    # Cập nhật thanh tiến trình
                    if currTimestamp > self.highestFrameNbr:
                        self.highestFrameNbr = currTimestamp
                        self.set_buffer(self.highestFrameNbr)

                    # Nếu Marker = 1 -> Frame đã hoàn thiện
                    if marker == 1:
                        # Sắp xếp lại các mảnh theo seqnum
                        self.currBuffer.sort(key=lambda x: x[0])
                        #----KIỂM TRA MẤT GÓI----
                        is_complete = True
                        # Kiểm tra xem các số Sequence có liên tiếp nhau không
                        for i in range(len(self.currBuffer) - 1):
                            curr_seq = self.currBuffer[i][0]
                            next_seq = self.currBuffer[i+1][0]
                        # Nếu gói sau không phải là số tiếp theo của gói trước -> Mất gói!
                        if next_seq != curr_seq + 1:
                            is_complete = False
                            break
                        # Ghép các mảnh lại chỉ khi đủ gói
                        if is_complete:
                            frame_data = b"".join([chunk[1] for chunk in self.currBuffer])
                        #------------------------
                        # Tự động gửi PAUSE nếu buffer hiển thị bị đầy
                        if self.frameBuffer.qsize() >= (self.MAX_BUFFER_SIZE - 20) and not self.isSeeking:
                            if self.requestSent != self.PAUSE and not self.auto_pause_sent:
                                self.auto_pause_sent = True 
                                self.sendRtspRequest(self.PAUSE)

                        #In để xác nhận Client đã nhận được frame
                        print(f"Current Frame Num: {currTimestamp}")

                        # Đẩy Frame hoàn chỉnh vào hàng đợi hiển thị
                        if not self.frameBuffer.full():
                            self.frameBuffer.put((currTimestamp, frame_data))
                        pass

            except socket.timeout:
                if self.teardownAcked == 1:
                    self.rtpSocket.close()
                    break
                continue
            except:
                if self.teardownAcked == 1:
                    self.rtpSocket.shutdown(socket.SHUT_RDWR)
                    self.rtpSocket.close()
                    break
    
    def start_play_loop(self):
        if not self.play_loop_active:
            self.play_loop_active = True
            self.consumeBufferedFrames()

    def consumeBufferedFrames(self):
        """Vòng lặp hiển thị video"""
        if self.state == self.PLAYING:
            
            # Gọi Server dậy nếu buffer vơi
            if self.frameBuffer.qsize() < 60 and self.auto_pause_sent:
                self.auto_pause_sent = False
                self.sendRtspRequest(self.PLAY)

            if not self.is_ui_paused:
                # Logic Buffering
                if self.isBuffering:
                    # Nếu đang seek từ server (Trường hợp 2), đợi có 1 frame là phát luôn
                    # Nếu đang chạy bình thường, đợi đủ ngưỡng mới phát
                    threshold = 1 if self.isSeeking else self.BUFFER_THRESHOLD
                    
                    if self.frameBuffer.qsize() > threshold:
                        self.isBuffering = False
                        self.isSeeking = False 
                    
                if not self.isBuffering:
                    if not self.frameBuffer.empty():
                        frameNum, frameData = self.frameBuffer.get()

                        self.frameNbr = frameNum
                        self.updateMovie(frameData)
                    else:
                        self.isBuffering = True 
            
            # Tính thời gian chờ (ms) = 1000 / FPS
            self.master.after(int(1000 / self.FPS), self.consumeBufferedFrames)
        else:
            self.play_loop_active = False

    def updateMovie(self, imageData):
        try:
            image_stream = io.BytesIO(imageData)

            # Resize ImageS
            original_image = Image.open(image_stream)
            resized_image = original_image.resize((self.GUI_WIDTH, self.GUI_HEIGHT))
            photo = ImageTk.PhotoImage(resized_image)
            # Cập nhật lên Lable
            self.label.configure(image = photo, height=self.GUI_HEIGHT) 
            self.label.image = photo
        except: 
            return

        self.set_progress(self.frameNbr)

        # Tránh lỗi chia cho 0 nếu FPS chưa set hoặc bằng 0
        safe_fps = self.FPS if self.FPS > 0 else 20
        currSeconds = int(self.frameNbr / safe_fps)
        totalSeconds = int(self.TOTAL_NO_FRAMES / safe_fps)
        if currSeconds > totalSeconds: currSeconds = totalSeconds

        # Tính thời gian
        currStr = f"{currSeconds // 60:02d}:{currSeconds % 60:02d}"
        totalStr = f"{totalSeconds // 60:02d}:{totalSeconds % 60:02d}"
        self.timeLabel.configure(text=f"{currStr} / {totalStr}")
        
    def connectToServer(self):
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' %self.serverAddr)
    
    def sendRtspRequest(self, requestCode, seekFrame=0): # Thêm seekFrame để xác định vị trí tua
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply).start()
            self.rtspSeq += 1
            request = f"SETUP {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nTransport: RTP/UDP; client_port= {self.rtpPort}"
            self.requestSent = self.SETUP
        
        elif requestCode == self.PLAY and (self.state == self.READY or self.state == self.PLAYING):
            self.rtspSeq += 1
            request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            if seekFrame > 0:
                request += f"\nFrame: {seekFrame}"
            self.requestSent = self.PLAY
            
        elif requestCode == self.PAUSE and (self.state == self.PLAYING or self.state == self.READY):
            self.rtspSeq += 1
            request = f"PAUSE {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.PAUSE
            
        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            self.rtspSeq += 1
            request = f"TEARDOWN {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.TEARDOWN
        else:
            return
        
        print(request)
        self.rtspSocket.send(request.encode("utf-8"))
    
    def recvRtspReply(self):
        while True:
            try:
                reply = self.rtspSocket.recv(1024)
                if reply: 
                    self.parseRtspReply(reply.decode("utf-8"))
                
                if self.requestSent == self.TEARDOWN:
                    self.rtspSocket.shutdown(socket.SHUT_RDWR)
                    self.rtspSocket.close()
                    break
            except:
                break
    
    def parseRtspReply(self, data):
        lines = data.split('\n')
        try: seqNum = int(lines[1].split(' ')[1])
        except: return
        
        if seqNum == self.rtspSeq:
            session = int(lines[2].split(' ')[1])
            if self.sessionId == 0: self.sessionId = session
            
            if self.sessionId == session:
                if int(lines[0].split(' ')[1]) == 200: 
                    if self.requestSent == self.SETUP:
                        self.state = self.READY
                        for line in lines:
                            if "TotalFrames" in line:
                                self.TOTAL_NO_FRAMES = int(line.split(' ')[1])
                                print(f"\nTotal Frames will get from Server: {self.TOTAL_NO_FRAMES}\n")
                        self.openRtpPort() 
                    
                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING
                        if not self.play_loop_active:
                            self.master.after(0, self.start_play_loop)
                    
                    elif self.requestSent == self.PAUSE:
                        # Logic: Khi nhận PAUSE, không làm gì cả, chỉ set event để flow control hoạt động
                        self.playEvent.set()
                    
                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT
                        self.teardownAcked = 1 
    
    def openRtpPort(self):
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 5*1024*1024) # 5MB
        self.rtpSocket.settimeout(0.5)
        try:
            self.state = self.READY
            self.rtpSocket.bind(("", self.rtpPort))
        except:
            tkMessageBox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' %self.rtpPort)