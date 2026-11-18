"""Descargador de YouTube para Android (Kivy + yt-dlp).

Formatos:
  - Vídeo (MP4): formato progresivo (vídeo y audio ya unidos, sin ffmpeg).
  - Audio (M4A): se guarda tal cual, sin conversión.
  - Audio (MP3): se descarga el audio y se convierte después.
        1) con el ejecutable `ffmpeg` si existe (útil al probar en el PC)
        2) con PyAV (`av`) si la app se compiló con soporte MP3
        3) si ninguno está disponible, se conserva el audio original y se avisa.
"""

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import certifi
from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.utils import platform

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import yt_dlp  # noqa: E402
from yt_dlp.utils import DownloadError  # noqa: E402

MODE_VIDEO, MODE_M4A, MODE_MP3 = "Vídeo (MP4)", "Audio (M4A)", "Audio (MP3)"
MODES = (MODE_VIDEO, MODE_M4A, MODE_MP3)
QUALITIES = {
    "Mejor disponible": None,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}

KV = """
BoxLayout:
    orientation: "vertical"
    padding: dp(16)
    spacing: dp(12)

    Label:
        text: "Descargador de YouTube"
        font_size: "22sp"
        bold: True
        size_hint_y: None
        height: dp(48)

    TextInput:
        id: url
        hint_text: "Pega aquí la URL del vídeo"
        multiline: False
        size_hint_y: None
        height: dp(48)
        write_tab: False

    Spinner:
        id: mode
        text: app.modes[0]
        values: app.modes
        size_hint_y: None
        height: dp(48)

    Spinner:
        id: quality
        text: "Mejor disponible"
        values: app.quality_names
        disabled: mode.text != app.modes[0]
        size_hint_y: None
        height: dp(48)

    Button:
        text: "Descargar"
        disabled: app.busy
        size_hint_y: None
        height: dp(54)
        on_release: app.start(url.text, mode.text, quality.text)

    ProgressBar:
        max: 100
        value: app.progress
        size_hint_y: None
        height: dp(12)

    Label:
        text: app.status
        text_size: self.width, None
        halign: "center"
        valign: "top"
"""


@dataclass(slots=True, frozen=True)
class Request:
    url: str
    folder: Path
    mode: str
    max_height: int | None


def download_dir() -> Path:
    """Carpeta de descargas de la app (no requiere permisos de almacenamiento)."""
    if platform == "android":
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        env = autoclass("android.os.Environment")
        folder = activity.getExternalFilesDir(env.DIRECTORY_DOWNLOADS)
        return Path(folder.getAbsolutePath())
    return Path.home() / "Downloads"


def build_options(req: Request, on_progress) -> dict:
    def hook(d: dict) -> None:
        match d.get("status"):
            case "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                pct = d.get("downloaded_bytes", 0) / total * 100 if total else 0.0
                speed = (d.get("_speed_str") or "").strip()
                on_progress(pct, f"Descargando… {pct:.0f}%  {speed}")
            case "finished":
                on_progress(100.0, "Guardando archivo…")

    opts = {
        "outtmpl": str(req.folder / "%(title).80s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
    }
    if req.mode == MODE_VIDEO:
        limit = f"[height<={req.max_height}]" if req.max_height else ""
        opts["format"] = f"best[ext=mp4]{limit}/best{limit}/best"
    else:  # M4A o MP3: se baja el audio tal cual; la conversión va aparte
        opts["format"] = "bestaudio[ext=m4a]/bestaudio"
    return opts


# --- Conversión a MP3 ------------------------------------------------------
def _find_mp3_encoder() -> str | None:
    import av

    for name in ("libmp3lame", "libshine", "mp3"):
        try:
            av.Codec(name, "w")
            return name
        except Exception:  # noqa: BLE001
            continue
    return None


def _convert_with_pyav(src: Path, dst: Path, on_progress) -> None:
    try:
        import av
    except ImportError as e:
        raise RuntimeError(
            "esta compilación no incluye soporte MP3 (falta PyAV)"
        ) from e

    encoder = _find_mp3_encoder()
    if encoder is None:
        raise RuntimeError("el ffmpeg incluido no tiene codificador MP3")

    with av.open(str(src)) as inp, av.open(str(dst), "w", format="mp3") as out:
        in_stream = inp.streams.audio[0]
        out_stream = out.add_stream(encoder, rate=44100)
        out_stream.layout = "stereo"
        out_stream.bit_rate = 192_000
        resampler = av.AudioResampler(format="s16p", layout="stereo", rate=44100)
        total = (inp.duration or 0) / 1_000_000  # segundos

        for frame in inp.decode(in_stream):
            for f in resampler.resample(frame):
                out.mux(out_stream.encode(f))
            if total and frame.time is not None:
                pct = min(frame.time / total * 100, 100)
                on_progress(pct, f"Convirtiendo a MP3… {pct:.0f}%")
        for f in resampler.resample(None):
            out.mux(out_stream.encode(f))
        out.mux(out_stream.encode(None))


def convert_to_mp3(src: Path, on_progress) -> Path:
    dst = src.with_suffix(".mp3")
    try:
        if ffmpeg := shutil.which("ffmpeg"):
            subprocess.run(
                [ffmpeg, "-y", "-i", str(src), "-vn", "-codec:a", "libmp3lame",
                 "-b:a", "192k", str(dst)],
                check=True, capture_output=True,
            )
        else:
            _convert_with_pyav(src, dst, on_progress)
    except Exception:
        dst.unlink(missing_ok=True)  # no dejar un MP3 a medias
        raise
    src.unlink(missing_ok=True)
    return dst


class YtApp(App):
    modes = MODES
    quality_names = list(QUALITIES)
    progress = NumericProperty(0)
    status = StringProperty("Listo.")
    busy = BooleanProperty(False)

    def build(self):
        self.title = "YT Downloader"
        return Builder.load_string(KV)

    def _set(self, progress: float | None = None, status: str | None = None,
             busy: bool | None = None) -> None:
        """Actualiza la UI desde cualquier hilo."""
        def apply(_dt):
            if progress is not None:
                self.progress = progress
            if status is not None:
                self.status = status
            if busy is not None:
                self.busy = busy
        Clock.schedule_once(apply)

    def start(self, url: str, mode: str, quality: str) -> None:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            self.status = "Introduce una URL completa de YouTube."
            return

        folder = download_dir()
        folder.mkdir(parents=True, exist_ok=True)
        req = Request(url, folder, mode, QUALITIES[quality])

        self.busy = True
        self.progress = 0
        self.status = "Iniciando descarga…"
        threading.Thread(target=self._worker, args=(req,), daemon=True).start()

    def _worker(self, req: Request) -> None:
        def progress(p: float, s: str) -> None:
            self._set(progress=p, status=s)

        try:
            with yt_dlp.YoutubeDL(build_options(req, progress)) as ydl:
                info = ydl.extract_info(req.url, download=True)
                downloads = info.get("requested_downloads") or []
                path = Path(downloads[0]["filepath"] if downloads else ydl.prepare_filename(info))
            title = info.get("title", "")

            if req.mode == MODE_MP3:
                self._set(0, "Convirtiendo a MP3…")
                try:
                    path = convert_to_mp3(path, progress)
                except Exception as e:  # noqa: BLE001
                    self._set(
                        0,
                        f"⚠ No se pudo convertir a MP3: {e}\n\n"
                        f"Se conservó el audio original:\n{path}",
                        False,
                    )
                    return

            self._set(100, f"✔ Completado: {title}\n\nGuardado en:\n{path.parent}", False)
        except DownloadError as e:
            self._set(0, f"✖ Error: {e}", False)
        except Exception as e:  # noqa: BLE001
            self._set(0, f"✖ Error inesperado: {e}", False)


if __name__ == "__main__":
    YtApp().run()
