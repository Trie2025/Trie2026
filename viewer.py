"""
摄像头查看端（教师机）
- 自动发现同局域网内的摄像头提供端
- 连接后可显示多路摄像头画面
- 可调节每路画面的清晰度（JPEG 质量）和分辨率
- 可正常断开某路连接
- 双击画面全屏、窗口大小自动适应、画面铺满窗格
- 左侧栏可滚动、圆角按钮、响应式布局
"""

import json
import queue
import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import cv2
import numpy as np
from PIL import Image, ImageTk


DISCOVERY_PORT = 50001
RECV_BUFFER = 4096
FRAME_QUEUE_MAX = 2

# 主题色
BG_COLOR = "#121212"
CARD_BG = "#1e1e1e"
CARD_HEADER = "#2a2a2a"
ACCENT = "#4A90D9"
ACCENT_HOVER = "#357ABD"
DANGER = "#c0392b"
DANGER_HOVER = "#e74c3c"
TEXT_COLOR = "#f0f0f0"
TEXT_SECONDARY = "#999999"
BORDER_COLOR = "#333333"

providers = {}
providers_lock = threading.Lock()
connections = {}
connections_lock = threading.Lock()
ui_queue = queue.Queue()

fullscreen_window = None
fullscreen_ip = None
resize_after_id = None


def recvall(sock, n):
    data = b""
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data


def send_cmd(sock, cmd):
    try:
        payload = json.dumps(cmd).encode("utf-8")
        sock.sendall(struct.pack("!I", len(payload)) + payload)
    except Exception:
        pass


def discovery_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except Exception:
        pass
    sock.bind(("0.0.0.0", DISCOVERY_PORT))
    sock.settimeout(1.0)

    while True:
        try:
            data, addr = sock.recvfrom(RECV_BUFFER)
            text = data.decode("utf-8", errors="ignore")
            if text.startswith("CAM_PROVIDER|"):
                parts = text.split("|")
                if len(parts) >= 4:
                    ip, port, hostname = parts[1], int(parts[2]), parts[3]
                    with providers_lock:
                        providers[ip] = {
                            "ip": ip,
                            "port": port,
                            "hostname": hostname,
                            "last_seen": time.time(),
                        }
                    ui_queue.put(("update_list", None))
        except socket.timeout:
            continue
        except Exception:
            break
    sock.close()


def cleanup_stale_providers():
    while True:
        time.sleep(5)
        now = time.time()
        with providers_lock:
            stale = [ip for ip, info in providers.items() if now - info["last_seen"] > 10]
            for ip in stale:
                del providers[ip]
        if stale:
            ui_queue.put(("update_list", None))


def stream_reader(ip, port, conn_info):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    try:
        sock.connect((ip, port))
        with connections_lock:
            conn_info["socket"] = sock
        send_cmd(sock, {"cmd": "quality", "value": conn_info["quality"]})
        send_cmd(sock, {"cmd": "resolution", "value": conn_info["resolution"]})

        while conn_info["running"]:
            len_bytes = recvall(sock, 4)
            if not len_bytes:
                break
            frame_len = struct.unpack("!I", len_bytes)[0]
            if frame_len > 5 * 1024 * 1024:
                break
            frame_data = recvall(sock, frame_len)
            if frame_data is None:
                break
            try:
                while conn_info["frame_queue"].qsize() >= FRAME_QUEUE_MAX:
                    conn_info["frame_queue"].get_nowait()
                conn_info["frame_queue"].put(frame_data)
            except queue.Empty:
                pass
    except Exception:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass
        conn_info["running"] = False
        ui_queue.put(("disconnected", ip))


def update_frame(video_label, conn_info):
    try:
        while not conn_info["frame_queue"].empty():
            data = conn_info["frame_queue"].get_nowait()
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb)
                video_label.update_idletasks()
                w, h = video_label.winfo_width(), video_label.winfo_height()
                if w > 1 and h > 1:
                    pil_img = pil_img.resize((w, h), Image.Resampling.LANCZOS)
                tk_img = ImageTk.PhotoImage(pil_img)
                video_label.configure(image=tk_img)
                video_label.image = tk_img
    except queue.Empty:
        pass
    except Exception:
        pass


def on_closing(root):
    with connections_lock:
        for ip, info in list(connections.items()):
            info["running"] = False
            try:
                info["socket"].close()
            except Exception:
                pass
    root.destroy()


class RoundedButton:
    """使用 Canvas 绘制的圆角按钮。"""
    def __init__(self, parent, text, command=None, width=120, height=34,
                 bg=ACCENT, hover=ACCENT_HOVER, fg="white", font=("Microsoft YaHei", 10)):
        self.command = command
        self.bg = bg
        self.hover = hover
        self.fg = fg
        self.canvas = tk.Canvas(parent, width=width, height=height,
                                bg=parent["bg"], highlightthickness=0, cursor="hand2")
        self.radius = height // 2
        self._draw(bg)
        self.text_id = self.canvas.create_text(width // 2, height // 2, text=text,
                                               fill=fg, font=font)
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click)

    def _draw(self, color):
        self.canvas.delete("bg")
        r = self.radius
        w = self.canvas.winfo_reqwidth()
        h = self.canvas.winfo_reqheight()
        self.canvas.create_oval(0, 0, r * 2, r * 2, fill=color, outline=color, tags="bg")
        self.canvas.create_oval(w - r * 2, 0, w, r * 2, fill=color, outline=color, tags="bg")
        self.canvas.create_oval(0, h - r * 2, r * 2, h, fill=color, outline=color, tags="bg")
        self.canvas.create_oval(w - r * 2, h - r * 2, w, h, fill=color, outline=color, tags="bg")
        self.canvas.create_rectangle(r, 0, w - r, h, fill=color, outline=color, tags="bg")
        self.canvas.create_rectangle(0, r, w, h - r, fill=color, outline=color, tags="bg")
        self.canvas.tag_lower("bg")

    def _on_enter(self, event):
        self._draw(self.hover)
        if hasattr(self, 'text_id'):
            self.canvas.tag_raise(self.text_id)

    def _on_leave(self, event):
        self._draw(self.bg)
        if hasattr(self, 'text_id'):
            self.canvas.tag_raise(self.text_id)

    def _on_click(self, event):
        if self.command:
            self.command()

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)


class ScrollableFrame:
    """可滚动框架。"""
    def __init__(self, parent, width, bg):
        self.container = tk.Frame(parent, width=width, bg=bg)
        self.container.pack_propagate(False)

        self.canvas = tk.Canvas(self.container, bg=bg, highlightthickness=0,
                                width=width - 12)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.scrollbar = tk.Scrollbar(self.container, orient=tk.VERTICAL,
                                      command=self.canvas.yview, bg=bg,
                                      troughcolor=bg, activebackground=ACCENT)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.frame = tk.Frame(self.canvas, bg=bg, width=width - 12)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.frame,
                                                       anchor=tk.NW, width=width - 12)

        self.frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_frame_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        if self.canvas.winfo_containing(event.x_root, event.y_root) == self.canvas:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def pack(self, **kwargs):
        self.container.pack(**kwargs)



def build_ui(root):
    global resize_after_id

    root.title("摄像头查看端")
    root.geometry("1450x900")
    root.minsize(900, 600)
    root.configure(bg=BG_COLOR)
    root.protocol("WM_DELETE_WINDOW", lambda: on_closing(root))

    title_font = tkfont.Font(family="Microsoft YaHei", size=14, weight="bold")
    small_font = tkfont.Font(family="Microsoft YaHei", size=9)
    btn_font = ("Microsoft YaHei", 10)

    sidebar_visible = True
    sidebar_width = max(280, min(340, root.winfo_screenwidth() // 6))

    # 顶部标题栏
    header_frame = tk.Frame(root, bg=CARD_HEADER, height=48)
    header_frame.pack(side=tk.TOP, fill=tk.X)
    header_frame.pack_propagate(False)

    menu_btn = tk.Label(header_frame, text="☰", bg=CARD_HEADER, fg=TEXT_COLOR,
                        font=("Microsoft YaHei", 14), cursor="hand2", padx=15)
    menu_btn.pack(side=tk.LEFT)
    menu_btn.bind("<Enter>", lambda e: menu_btn.config(fg=ACCENT))
    menu_btn.bind("<Leave>", lambda e: menu_btn.config(fg=TEXT_COLOR))

    tk.Label(header_frame, text="摄像头查看端", font=title_font,
             bg=CARD_HEADER, fg=TEXT_COLOR).pack(side=tk.LEFT, padx=(0, 20))

    conn_count_label = tk.Label(header_frame, text="连接数: 0", font=small_font,
                                bg=CARD_HEADER, fg=TEXT_SECONDARY)
    conn_count_label.pack(side=tk.RIGHT, padx=15)

    # 主区域：可拖动分隔条 + 可隐藏左侧栏
    main_paned = tk.PanedWindow(root, orient=tk.HORIZONTAL, bg=BG_COLOR,
                                sashrelief=tk.FLAT, sashwidth=6, bd=0)
    main_paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    left_wrapper = tk.Frame(main_paned, bg=BG_COLOR, width=sidebar_width)
    left_wrapper.pack_propagate(False)
    left_scroll = ScrollableFrame(left_wrapper, width=sidebar_width - 12, bg=BG_COLOR)
    left_scroll.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
    left_frame = left_scroll.frame

    right_frame = tk.Frame(main_paned, bg=BG_COLOR)

    main_paned.add(left_wrapper, minsize=240)
    main_paned.add(right_frame, minsize=400)

    # 左侧标题
    tk.Label(left_frame, text="摄像头查看端", font=title_font,
             bg=BG_COLOR, fg=TEXT_COLOR).pack(anchor=tk.W, pady=(8, 4), padx=8)
    tk.Label(left_frame, text="自动发现同局域网摄像头", font=small_font,
             bg=BG_COLOR, fg=TEXT_SECONDARY).pack(anchor=tk.W, pady=(0, 18), padx=8)

    # 摄像头列表卡片
    list_card = tk.Frame(left_frame, bg=CARD_BG, bd=0)
    list_card.pack(fill=tk.X, pady=(0, 12), padx=8)

    tk.Label(list_card, text="发现的摄像头", font=("Microsoft YaHei", 10, "bold"),
             bg=CARD_BG, fg=TEXT_COLOR).pack(anchor=tk.W, padx=12, pady=(14, 8))

    listbox = tk.Listbox(list_card, height=8, bd=0, highlightthickness=0,
                         bg=CARD_BG, fg=TEXT_COLOR, selectbackground=ACCENT,
                         selectforeground="white", font=("Microsoft YaHei", 10),
                         relief=tk.FLAT)
    listbox.pack(fill=tk.X, padx=12, pady=(0, 10))

    btn_frame = tk.Frame(list_card, bg=CARD_BG)
    btn_frame.pack(fill=tk.X, padx=12, pady=(0, 14))
    connect_btn = RoundedButton(btn_frame, "连接", None, width=110, height=32, font=btn_font)
    connect_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
    disconnect_btn = RoundedButton(btn_frame, "断开", None, width=110, height=32,
                                   bg="#5a5a5a", hover="#6a6a6a", font=btn_font)
    disconnect_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

    # 控制面板卡片
    ctrl_card = tk.Frame(left_frame, bg=CARD_BG, bd=0)
    ctrl_card.pack(fill=tk.X, pady=(0, 12), padx=8)

    tk.Label(ctrl_card, text="画面设置", font=("Microsoft YaHei", 10, "bold"),
             bg=CARD_BG, fg=TEXT_COLOR).pack(anchor=tk.W, padx=12, pady=(14, 10))

    tk.Label(ctrl_card, text="清晰度 (JPEG 质量)", font=small_font,
             bg=CARD_BG, fg=TEXT_SECONDARY).pack(anchor=tk.W, padx=12)

    quality_var = tk.IntVar(value=70)
    quality_scale = tk.Scale(ctrl_card, from_=10, to=95, orient=tk.HORIZONTAL,
                             variable=quality_var, bg=CARD_BG, fg=TEXT_COLOR,
                             troughcolor=BORDER_COLOR, highlightthickness=0,
                             bd=0, activebackground=ACCENT, showvalue=0)
    quality_scale.pack(fill=tk.X, padx=10, pady=(0, 4))

    quality_label = tk.Label(ctrl_card, text="70", font=small_font,
                             bg=CARD_BG, fg=TEXT_COLOR)
    quality_label.pack(anchor=tk.E, padx=12, pady=(0, 12))

    tk.Label(ctrl_card, text="分辨率", font=small_font,
             bg=CARD_BG, fg=TEXT_SECONDARY).pack(anchor=tk.W, padx=12)

    res_var = tk.StringVar(value="1280x720")
    res_menu = tk.OptionMenu(ctrl_card, res_var, "640x480", "1280x720", "1920x1080")
    res_menu.config(bg=CARD_BG, fg=TEXT_COLOR, activebackground=ACCENT,
                    activeforeground="white", highlightthickness=0, bd=0,
                    font=("Microsoft YaHei", 10))
    res_menu["menu"].config(bg=CARD_BG, fg=TEXT_COLOR, activebackground=ACCENT,
                            activeforeground="white")
    res_menu.pack(fill=tk.X, padx=12, pady=(4, 0))

    apply_btn = RoundedButton(ctrl_card, "应用设置", None, width=220, height=34, font=btn_font)
    apply_btn.pack(fill=tk.X, padx=12, pady=(16, 14))

    # 全局操作卡片
    global_card = tk.Frame(left_frame, bg=CARD_BG, bd=0)
    global_card.pack(fill=tk.X, padx=8)

    tk.Label(global_card, text="全局操作", font=("Microsoft YaHei", 10, "bold"),
             bg=CARD_BG, fg=TEXT_COLOR).pack(anchor=tk.W, padx=12, pady=(14, 10))

    disconnect_all_btn = RoundedButton(global_card, "断开全部", None, width=220, height=34,
                                        bg=DANGER, hover=DANGER_HOVER, font=btn_font)
    disconnect_all_btn.pack(fill=tk.X, padx=12, pady=(0, 14))

    # 右侧视频区
    video_container = tk.Frame(right_frame, bg=BG_COLOR)
    video_container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 0))

    status_var = tk.StringVar(value="就绪")
    status_bar = tk.Label(right_frame, textvariable=status_var, anchor=tk.W,
                          bg=CARD_BG, fg=TEXT_SECONDARY, font=small_font,
                          padx=12, pady=6)
    status_bar.pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 12), padx=12)

    def toggle_sidebar(event=None):
        nonlocal sidebar_visible
        if sidebar_visible:
            main_paned.forget(left_wrapper)
            sidebar_visible = False
        else:
            main_paned.add(left_wrapper, minsize=240)
            main_paned.paneconfig(left_wrapper, width=sidebar_width)
            sidebar_visible = True
        root.after(100, rearrange_videos)

    menu_btn.bind("<Button-1>", toggle_sidebar)

    def update_conn_count():
        with connections_lock:
            conn_count_label.config(text=f"连接数: {len(connections)}")

    def refresh_list():
        listbox.delete(0, tk.END)
        with providers_lock:
            items = sorted(providers.values(), key=lambda x: x["hostname"])
            for info in items:
                connected = "● " if info["ip"] in connections else "  "
                listbox.insert(tk.END, f"{connected}{info['hostname']} ({info['ip']})")
        update_conn_count()

    def get_selected_ip():
        sel = listbox.curselection()
        if not sel:
            return None
        with providers_lock:
            items = sorted(providers.values(), key=lambda x: x["hostname"])
            return items[sel[0]]["ip"] if sel[0] < len(items) else None

    def connect():
        ip = get_selected_ip()
        if not ip:
            status_var.set("请先选择一个摄像头")
            return
        with connections_lock:
            if ip in connections:
                status_var.set("已连接")
                return
            conn_info = {
                "running": True,
                "socket": None,
                "frame_queue": queue.Queue(),
                "quality": quality_var.get(),
                "resolution": res_var.get(),
                "card": None,
                "video_label": None,
                "header_label": None,
                "return_btn": None,
            }
            connections[ip] = conn_info

        port = providers.get(ip, {}).get("port", 50000)
        t = threading.Thread(target=stream_reader, args=(ip, port, conn_info), daemon=True)
        t.start()

        card = tk.Frame(video_container, bg=CARD_BG, bd=0)
        card.grid_propagate(False)

        header = tk.Frame(card, bg=CARD_HEADER, height=30)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)

        title = tk.Label(header, text=f"{providers.get(ip, {}).get('hostname', ip)}  ({ip})",
                         bg=CARD_HEADER, fg=TEXT_COLOR, font=("Microsoft YaHei", 9),
                         padx=10)
        title.pack(side=tk.LEFT)

        video_label = tk.Label(card, bg="black", cursor="hand2")
        video_label.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        video_label.bind("<Double-Button-1>", lambda e, target=ip: enter_fullscreen(target))

        # 右下角返回按钮
        return_btn = tk.Label(card, text="返回", bg=CARD_HEADER, fg=TEXT_SECONDARY,
                              font=("Microsoft YaHei", 8), cursor="hand2",
                              padx=8, pady=2)
        return_btn.place(relx=1.0, rely=1.0, x=-8, y=-8, anchor="se")
        return_btn.bind("<Enter>", lambda e: return_btn.config(fg="white", bg=ACCENT))
        return_btn.bind("<Leave>", lambda e: return_btn.config(fg=TEXT_SECONDARY, bg=CARD_HEADER))
        return_btn.bind("<Button-1>", lambda e, target=ip: disconnect_ip(target))

        conn_info["card"] = card
        conn_info["video_label"] = video_label
        conn_info["header_label"] = title
        conn_info["return_btn"] = return_btn

        rearrange_videos()
        refresh_list()
        status_var.set(f"正在连接 {ip}...")

    def disconnect():
        ip = get_selected_ip()
        if ip:
            disconnect_ip(ip)

    def disconnect_ip(ip):
        global fullscreen_ip, fullscreen_window
        with connections_lock:
            if ip not in connections:
                return
            info = connections.pop(ip)
            info["running"] = False
            sock = info.get("socket")
            if sock:
                try:
                    send_cmd(sock, {"cmd": "stop"})
                    sock.close()
                except Exception:
                    pass
            card = info.get("card")
            if card:
                card.destroy()
            if fullscreen_ip == ip and fullscreen_window:
                fullscreen_window.destroy()
                fullscreen_window = None
                fullscreen_ip = None
        rearrange_videos()
        refresh_list()
        status_var.set(f"已断开 {ip}")

    def disconnect_all():
        with connections_lock:
            ips = list(connections.keys())
        for ip in ips:
            disconnect_ip(ip)
        status_var.set("已断开全部摄像头")

    def apply_settings():
        ip = get_selected_ip()
        if not ip:
            status_var.set("请先选择一个摄像头")
            return
        with connections_lock:
            if ip not in connections:
                status_var.set("该摄像头未连接")
                return
            info = connections[ip]
            info["quality"] = quality_var.get()
            info["resolution"] = res_var.get()
            sock = info.get("socket")
            if sock:
                send_cmd(sock, {"cmd": "quality", "value": info["quality"]})
                send_cmd(sock, {"cmd": "resolution", "value": info["resolution"]})
        status_var.set(f"已向 {ip} 应用设置")

    def rearrange_videos():
        with connections_lock:
            cards = [info["card"] for info in connections.values() if info.get("card")]
        for widget in video_container.winfo_children():
            widget.grid_forget()
        n = len(cards)
        if n == 0:
            return
        cols = int(n ** 0.5) + (1 if int(n ** 0.5) ** 2 < n else 0)
        cols = max(1, cols)
        rows = (n + cols - 1) // cols
        for idx, card in enumerate(cards):
            card.grid(row=idx // cols, column=idx % cols, sticky="nsew", padx=5, pady=5)
        for c in range(cols):
            video_container.grid_columnconfigure(c, weight=1, uniform="col")
        for r in range(rows):
            video_container.grid_rowconfigure(r, weight=1, uniform="row")

    def on_resize(event=None):
        global resize_after_id
        if resize_after_id:
            root.after_cancel(resize_after_id)
        resize_after_id = root.after(200, rearrange_videos)

    def enter_fullscreen(ip):
        global fullscreen_window, fullscreen_ip
        with connections_lock:
            info = connections.get(ip)
            if not info or not info.get("video_label"):
                return

        if fullscreen_window:
            fullscreen_window.destroy()

        fullscreen_ip = ip
        fullscreen_window = tk.Toplevel(root)
        fullscreen_window.title("全屏查看")
        fullscreen_window.configure(bg="black")
        fullscreen_window.state("zoomed")
        fullscreen_window.attributes("-topmost", True)

        fs_label = tk.Label(fullscreen_window, bg="black", cursor="hand2")
        fs_label.pack(fill=tk.BOTH, expand=True)
        fs_label.bind("<Double-Button-1>", lambda e: exit_fullscreen())
        fullscreen_window.bind("<Escape>", lambda e: exit_fullscreen())
        fullscreen_window.protocol("WM_DELETE_WINDOW", exit_fullscreen)

        def update_fs():
            if not fullscreen_window or fullscreen_ip != ip:
                return
            with connections_lock:
                current = connections.get(ip)
            if current and current.get("video_label"):
                update_frame(fs_label, current)
            fullscreen_window.after(30, update_fs)

        update_fs()

    def exit_fullscreen():
        global fullscreen_window, fullscreen_ip
        if fullscreen_window:
            fullscreen_window.destroy()
        fullscreen_window = None
        fullscreen_ip = None

    def on_quality_change(*_):
        quality_label.config(text=str(quality_var.get()))

    quality_var.trace_add("write", on_quality_change)

    connect_btn.command = connect
    disconnect_btn.command = disconnect
    apply_btn.command = apply_settings
    disconnect_all_btn.command = disconnect_all

    video_container.bind("<Configure>", on_resize)
    root.bind("<Configure>", on_resize)

    def process_ui_queue():
        try:
            while True:
                msg_type, data = ui_queue.get_nowait()
                if msg_type == "update_list":
                    refresh_list()
                elif msg_type == "disconnected":
                    ip = data
                    with connections_lock:
                        if ip in connections:
                            info = connections.pop(ip)
                            card = info.get("card")
                            if card:
                                card.destroy()
                            if fullscreen_ip == ip and fullscreen_window:
                                fullscreen_window.destroy()
                                fullscreen_window = None
                                fullscreen_ip = None
                    rearrange_videos()
                    refresh_list()
                    status_var.set(f"{ip} 已断开")
        except queue.Empty:
            pass

        with connections_lock:
            for info in connections.values():
                if info.get("video_label"):
                    update_frame(info["video_label"], info)

        root.after(30, process_ui_queue)

    refresh_list()
    process_ui_queue()


def main():
    threading.Thread(target=discovery_listener, daemon=True).start()
    threading.Thread(target=cleanup_stale_providers, daemon=True).start()
    root = tk.Tk()
    build_ui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
