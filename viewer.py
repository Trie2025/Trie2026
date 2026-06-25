"""
摄像头查看端（教师机）
- 自动发现同局域网内的摄像头提供端
- 连接后可显示多路摄像头画面
- 可调节每路画面的清晰度（JPEG 质量）和分辨率
- 可正常断开某路连接
"""

import json
import queue
import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image, ImageTk


DISCOVERY_PORT = 50001
RECV_BUFFER = 4096
FRAME_QUEUE_MAX = 2

providers = {}          # ip -> {"ip", "port", "hostname", "last_seen"}
providers_lock = threading.Lock()
connections = {}        # ip -> {"thread", "running", "socket", "frame_queue", "quality", "resolution", "label"}
connections_lock = threading.Lock()
ui_queue = queue.Queue()


def recvall(sock, n):
    data = b""
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data


def send_cmd(sock, cmd):
    """发送 JSON 控制指令。"""
    try:
        payload = json.dumps(cmd).encode("utf-8")
        sock.sendall(struct.pack("!I", len(payload)) + payload)
    except Exception:
        pass


def discovery_listener():
    """监听 UDP 广播，维护摄像头端列表。"""
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
    """定期清理超过 10 秒未广播的提供者。"""
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
    """在独立线程中读取某路摄像头流。"""
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
                # 只保留最新一帧，降低延迟
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


def update_frame(label, conn_info):
    """从队列取帧并刷新 tkinter 组件。"""
    try:
        while not conn_info["frame_queue"].empty():
            data = conn_info["frame_queue"].get_nowait()
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb)
                # 等比缩放到标签大小
                label.update_idletasks()
                w, h = label.winfo_width(), label.winfo_height()
                if w > 1 and h > 1:
                    pil_img.thumbnail((w, h))
                tk_img = ImageTk.PhotoImage(pil_img)
                label.configure(image=tk_img)
                label.image = tk_img
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


def build_ui(root):
    root.title("摄像头查看端")
    root.geometry("1200x800")
    root.protocol("WM_DELETE_WINDOW", lambda: on_closing(root))

    # 左侧列表
    left_frame = ttk.Frame(root, width=250)
    left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
    left_frame.pack_propagate(False)

    ttk.Label(left_frame, text="发现的摄像头").pack(anchor=tk.W, pady=(0, 5))
    listbox = tk.Listbox(left_frame, height=15)
    listbox.pack(fill=tk.BOTH, expand=True)

    btn_frame = ttk.Frame(left_frame)
    btn_frame.pack(fill=tk.X, pady=5)
    connect_btn = ttk.Button(btn_frame, text="连接")
    connect_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
    disconnect_btn = ttk.Button(btn_frame, text="断开")
    disconnect_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

    # 控制区
    ctrl_frame = ttk.LabelFrame(left_frame, text="画面设置")
    ctrl_frame.pack(fill=tk.X, pady=10)

    ttk.Label(ctrl_frame, text="清晰度 (JPEG 质量)").pack(anchor=tk.W)
    quality_var = tk.IntVar(value=70)
    quality_scale = ttk.Scale(ctrl_frame, from_=10, to=95, orient=tk.HORIZONTAL, variable=quality_var)
    quality_scale.pack(fill=tk.X)
    quality_label = ttk.Label(ctrl_frame, text="70")
    quality_label.pack(anchor=tk.E)

    ttk.Label(ctrl_frame, text="分辨率").pack(anchor=tk.W, pady=(10, 0))
    res_var = tk.StringVar(value="1280x720")
    res_combo = ttk.Combobox(ctrl_frame, textvariable=res_var, values=["640x480", "1280x720", "1920x1080"], state="readonly")
    res_combo.pack(fill=tk.X)

    apply_btn = ttk.Button(ctrl_frame, text="应用设置")
    apply_btn.pack(fill=tk.X, pady=10)

    # 右侧画面区
    right_frame = ttk.Frame(root)
    right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

    video_container = ttk.Frame(right_frame)
    video_container.pack(fill=tk.BOTH, expand=True)

    status_var = tk.StringVar(value="就绪")
    status_bar = ttk.Label(right_frame, textvariable=status_var, relief=tk.SUNKEN, anchor=tk.W)
    status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def refresh_list():
        listbox.delete(0, tk.END)
        with providers_lock:
            items = sorted(providers.values(), key=lambda x: x["hostname"])
            for info in items:
                connected = "● " if info["ip"] in connections else "  "
                listbox.insert(tk.END, f"{connected}{info['hostname']} ({info['ip']})")

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
                "label": None,
            }
            connections[ip] = conn_info

        port = providers.get(ip, {}).get("port", 50000)
        t = threading.Thread(target=stream_reader, args=(ip, port, conn_info), daemon=True)
        t.start()

        # 创建视频标签
        label = tk.Label(video_container, bg="black", relief=tk.RIDGE, bd=2)
        conn_info["label"] = label
        rearrange_videos()
        refresh_list()
        status_var.set(f"正在连接 {ip}...")

    def disconnect():
        ip = get_selected_ip()
        if not ip:
            status_var.set("请先选择一个摄像头")
            return
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
            label = info.get("label")
            if label:
                label.destroy()
        rearrange_videos()
        refresh_list()
        status_var.set(f"已断开 {ip}")

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
            labels = [info["label"] for info in connections.values() if info.get("label")]
        for widget in video_container.winfo_children():
            widget.grid_forget()
        n = len(labels)
        if n == 0:
            return
        cols = int(n ** 0.5) + (1 if int(n ** 0.5) ** 2 < n else 0)
        cols = max(1, cols)
        for idx, label in enumerate(labels):
            label.grid(row=idx // cols, column=idx % cols, sticky="nsew", padx=2, pady=2)
        for c in range(cols):
            video_container.grid_columnconfigure(c, weight=1)
        for r in range((n + cols - 1) // cols):
            video_container.grid_rowconfigure(r, weight=1)

    def on_quality_change(*_):
        quality_label.config(text=str(quality_var.get()))

    quality_var.trace_add("write", on_quality_change)

    connect_btn.config(command=connect)
    disconnect_btn.config(command=disconnect)
    apply_btn.config(command=apply_settings)

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
                            label = info.get("label")
                            if label:
                                label.destroy()
                    rearrange_videos()
                    refresh_list()
                    status_var.set(f"{ip} 已断开")
        except queue.Empty:
            pass

        # 刷新所有画面
        with connections_lock:
            for info in connections.values():
                if info.get("label"):
                    update_frame(info["label"], info)

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
