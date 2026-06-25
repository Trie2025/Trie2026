"""
查看端（客户端）
- 连接局域网内的摄像头提供端，实时显示画面
- 可调节清晰度、分辨率
- 一键远程关闭并清除提供端程序
- 不保存任何录像
"""
import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
import cv2
import numpy as np


class CameraClient:
    def __init__(self):
        self.sock = None
        self.connected = False
        self.running = False
        self.recv_thread = None
        self.latest_frame = None
        self.lock = threading.Lock()

        # 默认设置
        self.quality = 70
        self.resolution = "640x480"
        self.fps = 30

        # 预设分辨率
        self.resolutions = ["320x240", "640x480", "800x600", "1024x768", "1280x720", "1920x1080"]

        # 构建GUI
        self.build_gui()

    def build_gui(self):
        self.root = tk.Tk()
        self.root.title("摄像头查看端")
        self.root.geometry("900x700")
        self.root.configure(bg="#1e1e2e")

        # 顶部控制栏
        top_frame = tk.Frame(self.root, bg="#2d2d3f", padx=10, pady=8)
        top_frame.pack(fill=tk.X)

        # 连接区域
        tk.Label(top_frame, text="服务器IP:", bg="#2d2d3f", fg="#cdd6f4", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.ip_entry = tk.Entry(top_frame, width=16, font=("Consolas", 10), bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4", relief=tk.FLAT)
        self.ip_entry.pack(side=tk.LEFT, padx=(0, 10))
        self.ip_entry.insert(0, "192.168.1.100")

        tk.Label(top_frame, text="端口:", bg="#2d2d3f", fg="#cdd6f4", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.port_entry = tk.Entry(top_frame, width=7, font=("Consolas", 10), bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4", relief=tk.FLAT)
        self.port_entry.pack(side=tk.LEFT, padx=(0, 15))
        self.port_entry.insert(0, "9090")

        self.connect_btn = tk.Button(top_frame, text="连接", command=self.toggle_connect,
                                      bg="#89b4fa", fg="#1e1e2e", font=("Microsoft YaHei", 10, "bold"),
                                      relief=tk.FLAT, padx=15, pady=3, cursor="hand2")
        self.connect_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.status_label = tk.Label(top_frame, text="● 未连接", bg="#2d2d3f", fg="#f38ba8", font=("Microsoft YaHei", 9))
        self.status_label.pack(side=tk.LEFT)

        # 中间视频区域
        self.video_frame = tk.Frame(self.root, bg="#11111b")
        self.video_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.canvas = tk.Canvas(self.video_frame, bg="#11111b", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # 底部控制栏
        bottom_frame = tk.Frame(self.root, bg="#2d2d3f", padx=10, pady=8)
        bottom_frame.pack(fill=tk.X)

        # 清晰度
        tk.Label(bottom_frame, text="清晰度:", bg="#2d2d3f", fg="#cdd6f4", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.quality_var = tk.IntVar(value=70)
        self.quality_scale = ttk.Scale(bottom_frame, from_=10, to=100, variable=self.quality_var,
                                        orient=tk.HORIZONTAL, length=150, command=self.on_quality_change)
        self.quality_scale.pack(side=tk.LEFT, padx=(0, 5))
        self.quality_label = tk.Label(bottom_frame, text="70%", bg="#2d2d3f", fg="#a6adc8", font=("Consolas", 9))
        self.quality_label.pack(side=tk.LEFT, padx=(0, 20))

        # 分辨率
        tk.Label(bottom_frame, text="分辨率:", bg="#2d2d3f", fg="#cdd6f4", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.res_var = tk.StringVar(value="640x480")
        self.res_combo = ttk.Combobox(bottom_frame, textvariable=self.res_var, values=self.resolutions,
                                       state="readonly", width=10, font=("Consolas", 9))
        self.res_combo.pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(bottom_frame, text="应用", command=self.on_resolution_change,
                  bg="#45475a", fg="#cdd6f4", font=("Microsoft YaHei", 9), relief=tk.FLAT, padx=8, cursor="hand2").pack(side=tk.LEFT, padx=(0, 20))

        # 一键清除按钮
        self.kill_btn = tk.Button(bottom_frame, text="一键清除提供端", command=self.on_kill,
                                   bg="#f38ba8", fg="#1e1e2e", font=("Microsoft YaHei", 10, "bold"),
                                   relief=tk.FLAT, padx=15, pady=3, cursor="hand2")
        self.kill_btn.pack(side=tk.RIGHT)

        # FPS显示
        self.fps_label = tk.Label(self.video_frame, text="", bg="#11111b", fg="#a6e3a1", font=("Consolas", 10))
        self.fps_label.place(relx=0.0, rely=1.0, anchor="sw")

    def on_quality_change(self, val):
        q = int(float(val))
        self.quality_label.config(text=f"{q}%")
        if self.connected and self.sock:
            self.send_command(f"QUALITY:{q}")

    def on_resolution_change(self):
        res = self.res_var.get()
        if self.connected and self.sock:
            self.send_command(f"RESOLUTION:{res}")

    def send_command(self, cmd):
        try:
            if self.sock:
                self.sock.sendall(cmd.encode("utf-8").ljust(64, b"\x00"))
        except Exception:
            pass

    def toggle_connect(self):
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        ip = self.ip_entry.get().strip()
        port_str = self.port_entry.get().strip()
        if not ip or not port_str:
            messagebox.showwarning("提示", "请输入服务器IP和端口")
            return
        try:
            port = int(port_str)
        except ValueError:
            messagebox.showwarning("提示", "端口格式错误")
            return

        self.status_label.config(text="● 连接中...", fg="#f9e2af")
        self.root.update()

        def do_connect():
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(5.0)
                self.sock.connect((ip, port))
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.connected = True
                self.running = True
                self.root.after(0, lambda: self.status_label.config(text="● 已连接", fg="#a6e3a1"))
                self.root.after(0, lambda: self.connect_btn.config(text="断开", bg="#f38ba8"))
                self.recv_thread = threading.Thread(target=self.recv_loop, daemon=True)
                self.recv_thread.start()
                self.root.after(30, self.update_frame)
            except Exception as e:
                self.root.after(0, lambda: self.status_label.config(text=f"● 连接失败", fg="#f38ba8"))
                self.root.after(0, lambda: messagebox.showerror("错误", f"连接失败:\n{e}"))

        threading.Thread(target=do_connect, daemon=True).start()

    def disconnect(self):
        self.running = False
        self.connected = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        self.status_label.config(text="● 未连接", fg="#f38ba8")
        self.connect_btn.config(text="连接", bg="#89b4fa")
        self.canvas.delete("all")

    def recv_loop(self):
        """接收帧数据"""
        frame_count = 0
        last_fps_time = time.time()
        while self.running:
            try:
                # 读取4字节长度头
                header = self.recv_exact(4)
                if not header:
                    break
                length = struct.unpack(">I", header)[0]
                if length > 10 * 1024 * 1024:  # 超过10MB丢弃
                    continue
                # 读取帧数据
                data = self.recv_exact(length)
                if not data:
                    break
                # 解码JPEG
                arr = np.frombuffer(data, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    with self.lock:
                        self.latest_frame = frame
                    frame_count += 1
                    now = time.time()
                    if now - last_fps_time >= 1.0:
                        self.current_fps = frame_count / (now - last_fps_time)
                        frame_count = 0
                        last_fps_time = now
            except Exception:
                break
        if self.running:
            self.root.after(0, self.disconnect)

    def recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            except socket.timeout:
                continue
            except Exception:
                return None
        return buf

    def update_frame(self):
        """更新画布显示"""
        if not self.running:
            return
        with self.lock:
            frame = self.latest_frame
        if frame is not None:
            # 适配画布大小
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            if cw > 1 and ch > 1:
                h, w = frame.shape[:2]
                scale = min(cw / w, ch / h)
                nw, nh = int(w * scale), int(h * scale)
                frame = cv2.resize(frame, (nw, nh))
            # BGR -> RGB -> PhotoImage
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            self.photo = ImageTk.PhotoImage(img)
            self.canvas.delete("all")
            self.canvas.create_image(cw // 2, ch // 2, image=self.photo, anchor="center")

            # 显示FPS
            if hasattr(self, "current_fps"):
                self.fps_label.config(text=f"FPS: {self.current_fps:.1f}")

        self.root.after(30, self.update_frame)

    def on_kill(self):
        """一键清除提供端"""
        if not self.connected:
            messagebox.showwarning("提示", "请先连接服务器")
            return
        result = messagebox.askyesno("确认", "确定要远程关闭并清除提供端程序吗？\n此操作不可恢复。")
        if result:
            self.send_command("KILL")
            self.status_label.config(text="● 已发送清除指令", fg="#f9e2af")
            self.kill_btn.config(state=tk.DISABLED)
            time.sleep(1)
            self.disconnect()
            self.status_label.config(text="● 提供端已清除", fg="#a6adc8")
            self.kill_btn.config(state=tk.NORMAL)
            messagebox.showinfo("完成", "提供端程序已远程关闭并清除")

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    def on_close(self):
        self.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    client = CameraClient()
    client.run()
