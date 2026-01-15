class VideoStream:
    def __init__(self, filename):
        self.filename = filename
        self.frameNum = 0
        self.frames = []
        self.load_frames_from_file()

    def load_frames_from_file(self):
        """Load toàn bộ video vào RAM."""
        try:
            with open(self.filename, 'rb') as f:
                data = f.read()
                start = 0
                while True:
                    start_pos = data.find(b'\xff\xd8', start)
                    if start_pos == -1: break
                    end_pos = data.find(b'\xff\xd9', start_pos)
                    if end_pos == -1: break
                    
                    jpg_data = data[start_pos : end_pos + 2]
                    self.frames.append(jpg_data)
                    start = end_pos + 2
            print(f"Loaded {len(self.frames)} frames from file {self.filename} to RAM\n")
        except IOError:
            raise IOError

    def nextFrame(self):
        """Lấy frame tiếp theo."""
        if self.frameNum < len(self.frames):
            data = self.frames[self.frameNum]
            self.frameNum += 1
            return data
        return None
        
    def frameNbr(self):
        """Lấy số thứ tự frame hiện tại."""
        return self.frameNum
    
    def totalFrames(self):
        """Trả về tổng số frame."""
        return len(self.frames)
    
    def seek(self, frameNumber):
        """Nhảy đến frame chỉ định."""
        if 0 <= frameNumber < len(self.frames):
            self.frameNum = frameNumber