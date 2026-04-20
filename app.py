import json
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from datetime import datetime, timezone, timedelta

import requests
from tkcalendar import DateEntry


PARAM_TYPES = ["string", "int", "double", "boolean", "date"]
CONFIG_FILENAME = "config.json"
KST = timezone(timedelta(hours=9))


def current_iso_ms_utc() -> str:
    """Return current UTC time as 'YYYY-MM-DDTHH:MM:SS.sssZ' (= current KST moment in UTC)."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def kst_minute_input_to_utc_iso(raw: str) -> str:
    """Parse 'YYYY-MM-DD HH:MM' as KST, convert to UTC, format as 'YYYY-MM-DDTHH:MM:00.000Z'."""
    kst_dt = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    utc_dt = kst_dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:00.000Z")


def coerce_value(type_name: str, raw: str):
    """Convert string input into the chosen type. Raises ValueError with a friendly message."""
    raw = raw.strip()
    if type_name == "string":
        return raw
    if type_name == "int":
        return int(raw)
    if type_name == "double":
        return float(raw)
    if type_name == "boolean":
        low = raw.lower()
        if low in ("true", "1", "yes", "y"):
            return True
        if low in ("false", "0", "no", "n"):
            return False
        raise ValueError(f"boolean 값은 true/false 여야 합니다: {raw!r}")
    if type_name == "date":
        datetime.strptime(raw, "%Y-%m-%d")
        return raw
    raise ValueError(f"알 수 없는 타입: {type_name}")


class ParamRow:
    """One row in the parameters table: key / type / value / remove."""

    def __init__(self, parent, on_remove):
        self.frame = ttk.Frame(parent)
        self.key_var = tk.StringVar()
        self.type_var = tk.StringVar(value="string")
        self.value_var = tk.StringVar()

        self.key_entry = ttk.Entry(self.frame, textvariable=self.key_var, width=18)
        self.type_combo = ttk.Combobox(
            self.frame,
            textvariable=self.type_var,
            values=PARAM_TYPES,
            width=9,
            state="readonly",
        )
        self.value_entry = ttk.Entry(self.frame, textvariable=self.value_var, width=32)
        self.remove_btn = ttk.Button(self.frame, text="X", width=3, command=lambda: on_remove(self))

        self.key_entry.grid(row=0, column=0, padx=2, pady=2, sticky="ew")
        self.type_combo.grid(row=0, column=1, padx=2, pady=2)
        self.value_entry.grid(row=0, column=2, padx=2, pady=2, sticky="ew")
        self.remove_btn.grid(row=0, column=3, padx=2, pady=2)

        self.frame.columnconfigure(0, weight=1)
        self.frame.columnconfigure(2, weight=2)

    def pack(self, **kw):
        self.frame.pack(**kw)

    def destroy(self):
        self.frame.destroy()

    def to_dict(self):
        return {"key": self.key_var.get(), "type": self.type_var.get(), "value": self.value_var.get()}

    def load(self, data):
        self.key_var.set(data.get("key", ""))
        self.type_var.set(data.get("type", "string"))
        self.value_var.set(data.get("value", ""))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("API Request Sender")
        self.geometry("900x750")
        self.minsize(700, 600)

        self.param_rows: list[ParamRow] = []
        self.image_paths: list[str] = []

        self.date_key_var = tk.StringVar(value="date")
        self.use_current_time_var = tk.BooleanVar(value=True)
        self.hour_var = tk.StringVar(value="00")
        self.minute_var = tk.StringVar(value="00")

        self.access_key_name_var = tk.StringVar(value="access-key")
        self.access_key_var = tk.StringVar()
        self.secret_key_name_var = tk.StringVar(value="access-secret")
        self.secret_key_var = tk.StringVar()
        self.show_secret_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._load_config_if_exists()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # --- URL / Method / Image field name ---
        top = ttk.LabelFrame(self, text="요청 설정")
        top.pack(fill="x", **pad)

        ttk.Label(top, text="URL").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.url_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.url_var).grid(row=0, column=1, columnspan=3, sticky="ew", padx=4, pady=4)

        ttk.Label(top, text="Method").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.method_var = tk.StringVar(value="POST")
        ttk.Combobox(
            top,
            textvariable=self.method_var,
            values=["POST", "PUT", "PATCH"],
            state="readonly",
            width=8,
        ).grid(row=1, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(top, text="이미지 필드명").grid(row=1, column=2, sticky="e", padx=4, pady=4)
        self.image_field_var = tk.StringVar(value="file")
        ttk.Entry(top, textvariable=self.image_field_var, width=20).grid(row=1, column=3, sticky="w", padx=4, pady=4)

        ttk.Label(top, text="Timeout (초)").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        self.timeout_var = tk.StringVar(value="30")
        ttk.Entry(top, textvariable=self.timeout_var, width=10).grid(row=2, column=1, sticky="w", padx=4, pady=4)

        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        # --- Credentials (optional) ---
        cred_box = ttk.LabelFrame(self, text="인증 정보 (HTTP 헤더로 전송) — 헤더명 비우면 전송 안 됨")
        cred_box.pack(fill="x", **pad)

        ttk.Label(cred_box, text="Access Key 헤더명").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(cred_box, textvariable=self.access_key_name_var, width=20).grid(
            row=0, column=1, sticky="w", padx=4, pady=4
        )
        ttk.Label(cred_box, text="값").grid(row=0, column=2, sticky="e", padx=4, pady=4)
        ttk.Entry(cred_box, textvariable=self.access_key_var).grid(
            row=0, column=3, sticky="ew", padx=4, pady=4
        )

        ttk.Label(cred_box, text="Secret Key 헤더명").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(cred_box, textvariable=self.secret_key_name_var, width=20).grid(
            row=1, column=1, sticky="w", padx=4, pady=4
        )
        ttk.Label(cred_box, text="값").grid(row=1, column=2, sticky="e", padx=4, pady=4)
        self.secret_entry = ttk.Entry(cred_box, textvariable=self.secret_key_var, show="•")
        self.secret_entry.grid(row=1, column=3, sticky="ew", padx=4, pady=4)
        ttk.Checkbutton(
            cred_box,
            text="Secret 보기",
            variable=self.show_secret_var,
            command=self._toggle_secret_visibility,
        ).grid(row=1, column=4, sticky="w", padx=4, pady=4)

        cred_box.columnconfigure(3, weight=1)

        # --- Date (required) ---
        date_box = ttk.LabelFrame(self, text="Date (필수) — 포맷: 2026-04-20T15:30:45.123Z")
        date_box.pack(fill="x", **pad)

        ttk.Label(date_box, text="Date 필드명").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(date_box, textvariable=self.date_key_var, width=20).grid(
            row=0, column=1, sticky="w", padx=4, pady=4
        )
        ttk.Checkbutton(
            date_box,
            text="현재시간으로 보내기 (KST)",
            variable=self.use_current_time_var,
            command=self._toggle_date_entry,
        ).grid(row=0, column=2, columnspan=3, sticky="w", padx=10, pady=4)

        ttk.Label(date_box, text="날짜 (KST)").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.date_picker = DateEntry(
            date_box,
            width=14,
            date_pattern="yyyy-mm-dd",
            firstweekday="sunday",
        )
        self.date_picker.grid(row=1, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(date_box, text="시:분 (KST)").grid(row=1, column=2, sticky="e", padx=4, pady=4)

        vcmd_hour = (self.register(lambda p: self._validate_int_range(p, 23)), "%P")
        vcmd_minute = (self.register(lambda p: self._validate_int_range(p, 59)), "%P")

        self.hour_spin = ttk.Spinbox(
            date_box,
            from_=0,
            to=23,
            width=4,
            format="%02.0f",
            textvariable=self.hour_var,
            validate="key",
            validatecommand=vcmd_hour,
        )
        self.hour_spin.grid(row=1, column=3, sticky="w", padx=2, pady=4)
        ttk.Label(date_box, text=":").grid(row=1, column=4, padx=1)
        self.minute_spin = ttk.Spinbox(
            date_box,
            from_=0,
            to=59,
            width=4,
            format="%02.0f",
            textvariable=self.minute_var,
            validate="key",
            validatecommand=vcmd_minute,
        )
        self.minute_spin.grid(row=1, column=5, sticky="w", padx=2, pady=4)

        self._toggle_date_entry()

        # --- Params ---
        params_box = ttk.LabelFrame(self, text="폼 파라미터")
        params_box.pack(fill="both", expand=True, **pad)

        params_hdr = ttk.Frame(params_box)
        params_hdr.pack(fill="x")
        ttk.Label(params_hdr, text="Key", width=18).grid(row=0, column=0, padx=2)
        ttk.Label(params_hdr, text="Type", width=9).grid(row=0, column=1, padx=2)
        ttk.Label(params_hdr, text="Value", width=32).grid(row=0, column=2, padx=2)

        self.params_container = ttk.Frame(params_box)
        self.params_container.pack(fill="both", expand=True)

        ttk.Button(params_box, text="+ 파라미터 추가", command=self.add_param_row).pack(anchor="w", padx=4, pady=4)

        # --- Images ---
        img_box = ttk.LabelFrame(self, text="이미지 파일 (여러 개 선택 가능 — 파일당 1요청)")
        img_box.pack(fill="x", **pad)

        btns = ttk.Frame(img_box)
        btns.pack(fill="x")
        ttk.Button(btns, text="이미지 선택", command=self.pick_images).pack(side="left", padx=4, pady=4)
        ttk.Button(btns, text="목록 비우기", command=self.clear_images).pack(side="left", padx=4, pady=4)
        self.img_count_var = tk.StringVar(value="선택된 파일: 0개")
        ttk.Label(btns, textvariable=self.img_count_var).pack(side="left", padx=10)

        self.img_listbox = tk.Listbox(img_box, height=5)
        self.img_listbox.pack(fill="x", padx=4, pady=4)

        # --- Actions + log ---
        actions = ttk.Frame(self)
        actions.pack(fill="x", **pad)
        ttk.Button(actions, text="설정 저장", command=self.save_config).pack(side="left", padx=4)
        ttk.Button(actions, text="설정 불러오기", command=self.load_config_dialog).pack(side="left", padx=4)
        self.send_btn = ttk.Button(actions, text="요청 보내기", command=self.start_send)
        self.send_btn.pack(side="right", padx=4)

        log_box = ttk.LabelFrame(self, text="로그")
        log_box.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(log_box, height=10, wrap="word")
        self.log.pack(fill="both", expand=True, padx=4, pady=4)
        self.log.configure(state="disabled")

    # ----- row management -----

    def add_param_row(self, data=None):
        row = ParamRow(self.params_container, on_remove=self._remove_param)
        row.pack(fill="x")
        if data:
            row.load(data)
        self.param_rows.append(row)

    def _remove_param(self, row):
        row.destroy()
        self.param_rows.remove(row)

    # ----- credentials -----

    def _toggle_secret_visibility(self):
        self.secret_entry.configure(show="" if self.show_secret_var.get() else "•")

    # ----- date toggle -----

    def _toggle_date_entry(self):
        state = "disabled" if self.use_current_time_var.get() else "normal"
        self.date_picker.configure(state=state)
        self.hour_spin.configure(state=state)
        self.minute_spin.configure(state=state)

    @staticmethod
    def _validate_int_range(proposed: str, max_value: int) -> bool:
        if proposed == "":
            return True
        if len(proposed) > 2 or not proposed.isdigit():
            return False
        return int(proposed) <= max_value

    # ----- image list -----

    def pick_images(self):
        paths = filedialog.askopenfilenames(
            title="이미지 선택",
            filetypes=[
                ("이미지 파일", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("모든 파일", "*.*"),
            ],
        )
        if not paths:
            return
        for p in paths:
            if p not in self.image_paths:
                self.image_paths.append(p)
                self.img_listbox.insert("end", p)
        self.img_count_var.set(f"선택된 파일: {len(self.image_paths)}개")

    def clear_images(self):
        self.image_paths.clear()
        self.img_listbox.delete(0, "end")
        self.img_count_var.set("선택된 파일: 0개")

    # ----- config save/load -----

    def _collect_config(self):
        return {
            "url": self.url_var.get(),
            "method": self.method_var.get(),
            "image_field": self.image_field_var.get(),
            "timeout": self.timeout_var.get(),
            "date_key": self.date_key_var.get(),
            "use_current_time": self.use_current_time_var.get(),
            "date": self.date_picker.get_date().isoformat(),
            "hour": self.hour_var.get(),
            "minute": self.minute_var.get(),
            "access_key_name": self.access_key_name_var.get(),
            "access_key": self.access_key_var.get(),
            "secret_key_name": self.secret_key_name_var.get(),
            "secret_key": self.secret_key_var.get(),
            "params": [p.to_dict() for p in self.param_rows],
            "image_paths": list(self.image_paths),
        }

    def _apply_config(self, cfg):
        self.url_var.set(cfg.get("url", ""))
        self.method_var.set(cfg.get("method", "POST"))
        self.image_field_var.set(cfg.get("image_field", "file"))
        self.timeout_var.set(str(cfg.get("timeout", "30")))
        self.date_key_var.set(cfg.get("date_key", "date"))
        self.use_current_time_var.set(cfg.get("use_current_time", True))
        date_str = cfg.get("date", "")
        if date_str:
            try:
                self.date_picker.set_date(datetime.strptime(date_str, "%Y-%m-%d").date())
            except ValueError:
                pass
        self.hour_var.set(cfg.get("hour", "00"))
        self.minute_var.set(cfg.get("minute", "00"))
        self._toggle_date_entry()

        self.access_key_name_var.set(cfg.get("access_key_name", "access_key"))
        self.access_key_var.set(cfg.get("access_key", ""))
        self.secret_key_name_var.set(cfg.get("secret_key_name", "secret_key"))
        self.secret_key_var.set(cfg.get("secret_key", ""))

        for row in list(self.param_rows):
            self._remove_param(row)
        for p in cfg.get("params", []):
            self.add_param_row(p)

        self.clear_images()
        for p in cfg.get("image_paths", []):
            if os.path.exists(p):
                self.image_paths.append(p)
                self.img_listbox.insert("end", p)
        self.img_count_var.set(f"선택된 파일: {len(self.image_paths)}개")

    def save_config(self):
        path = filedialog.asksaveasfilename(
            title="설정 저장",
            defaultextension=".json",
            initialfile=CONFIG_FILENAME,
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._collect_config(), f, ensure_ascii=False, indent=2)
        self._log(f"설정 저장됨: {path}")

    def load_config_dialog(self):
        path = filedialog.askopenfilename(
            title="설정 불러오기",
            filetypes=[("JSON", "*.json"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        self._load_config_file(path)

    def _default_config_path(self):
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, CONFIG_FILENAME)

    def _load_config_if_exists(self):
        default_path = self._default_config_path()
        if os.path.exists(default_path):
            self._load_config_file(default_path)

    def _on_close(self):
        try:
            with open(self._default_config_path(), "w", encoding="utf-8") as f:
                json.dump(self._collect_config(), f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        self.destroy()

    def _load_config_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self._apply_config(cfg)
            self._log(f"설정 불러옴: {path}")
        except Exception as e:
            messagebox.showerror("설정 불러오기 실패", str(e))

    # ----- logging -----

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ----- sending -----

    def start_send(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("입력 필요", "URL을 입력하세요.")
            return
        if not self.image_paths:
            if not messagebox.askyesno("확인", "선택된 이미지가 없습니다. 이미지 없이 1회만 요청할까요?"):
                return

        try:
            timeout = float(self.timeout_var.get())
        except ValueError:
            messagebox.showerror("입력 오류", "Timeout은 숫자여야 합니다.")
            return

        date_key = self.date_key_var.get().strip()
        if not date_key:
            messagebox.showwarning("입력 필요", "Date 필드명을 입력하세요.")
            return

        use_current = self.use_current_time_var.get()
        static_date = None
        if not use_current:
            try:
                date_obj = self.date_picker.get_date()
                hour = int(self.hour_var.get())
                minute = int(self.minute_var.get())
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError("hour/minute out of range")
                raw = f"{date_obj.strftime('%Y-%m-%d')} {hour:02d}:{minute:02d}"
                static_date = kst_minute_input_to_utc_iso(raw)
            except (ValueError, AttributeError):
                messagebox.showerror(
                    "Date 오류",
                    "날짜와 시(0-23)·분(0-59)을 올바르게 입력하세요.",
                )
                return

        try:
            data_fields = {}
            for row in self.param_rows:
                key = row.key_var.get().strip()
                if not key:
                    continue
                data_fields[key] = coerce_value(row.type_var.get(), row.value_var.get())
        except ValueError as e:
            messagebox.showerror("파라미터 오류", str(e))
            return

        headers = {}
        ak_name = self.access_key_name_var.get().strip()
        ak_value = self.access_key_var.get().strip()
        if ak_name and ak_value:
            headers[ak_name] = ak_value

        sk_name = self.secret_key_name_var.get().strip()
        sk_value = self.secret_key_var.get().strip()
        if sk_name and sk_value:
            headers[sk_name] = sk_value

        self.send_btn.configure(state="disabled")
        thread = threading.Thread(
            target=self._send_worker,
            args=(
                url,
                self.method_var.get(),
                headers,
                data_fields,
                self.image_field_var.get().strip() or "file",
                timeout,
                date_key,
                use_current,
                static_date,
            ),
            daemon=True,
        )
        thread.start()

    def _send_worker(self, url, method, headers, data_fields, image_field, timeout, date_key, use_current, static_date):
        try:
            # requests needs strings for multipart form fields
            base_form = {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in data_fields.items()}

            def build_form():
                form = dict(base_form)
                form[date_key] = current_iso_ms_utc() if use_current else static_date
                return form

            if not self.image_paths:
                form_data = build_form()
                self._log(f"[1/1] 이미지 없이 요청 → {method} {url}  ({date_key}={form_data[date_key]})")
                self._do_request(method, url, headers, form_data, None, timeout)
            else:
                total = len(self.image_paths)
                for idx, path in enumerate(self.image_paths, 1):
                    form_data = build_form()
                    self._log(
                        f"[{idx}/{total}] {os.path.basename(path)} → {method} {url}  ({date_key}={form_data[date_key]})"
                    )
                    try:
                        with open(path, "rb") as f:
                            files = {image_field: (os.path.basename(path), f)}
                            self._do_request(method, url, headers, form_data, files, timeout)
                    except FileNotFoundError:
                        self._log(f"  파일 없음: {path}")
        finally:
            self.after(0, lambda: self.send_btn.configure(state="normal"))

    def _do_request(self, method, url, headers, data, files, timeout):
        try:
            resp = requests.request(
                method=method,
                url=url,
                headers=headers or None,
                data=data or None,
                files=files,
                timeout=timeout,
            )
            body = resp.text
            if len(body) > 500:
                body = body[:500] + "... (생략)"
            self._log(f"  ← {resp.status_code}  {body}")
        except requests.RequestException as e:
            self._log(f"  요청 실패: {e}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
