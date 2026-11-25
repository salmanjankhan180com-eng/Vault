import os, json, hashlib, time, threading, queue, shutil, zipfile, base64, mimetypes
from kivy.app import App
from kivy.cache import Cache
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle, Rectangle, Ellipse, Line
from kivy.metrics import dp
from kivy.utils import get_color_from_hex as hx, escape_markup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.image import Image, AsyncImage
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition

try:
    from kivy.uix.video import Video
except Exception:
    Video = None

try:  # chhote thumbnails ke liye (Pydroid mein aam taur par hota hai)
    from PIL import Image as PILImage
except Exception:
    PILImage = None

BG = "#0D0D12"
Window.clearcolor = hx(BG)
ACCENT, CARD, RED, GREEN = "#6C4DFF", "#1E1E2A", "#E5484D", "#2FB26B"
SOFT, MUTED, NAVBG = "#2A2A3C", "#9A9AB0", "#14141C"
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
VID_EXTS = (".mp4", ".mkv", ".3gp", ".mov", ".avi", ".webm")
ALL_EXTS = IMG_EXTS + VID_EXTS
ROOT = "/storage/emulated/0"
RESTORE_DIR = "/storage/emulated/0/Pictures/VaultRestored"
AUTOLOCK_OPTS = [("Off", 0), ("15 sec", 15), ("30 sec", 30), ("1 min", 60), ("5 min", 300)]
MAGIC = b"PVT1"
CHUNK = 1 << 18

def ask_android_permissions():
    """APK mein photos/videos ki permission maangta hai (Pydroid mein ignore)."""
    try:
        from android.permissions import request_permissions
        request_permissions([
            "android.permission.READ_MEDIA_IMAGES",
            "android.permission.READ_MEDIA_VIDEO",
            "android.permission.READ_EXTERNAL_STORAGE",
            "android.permission.WRITE_EXTERNAL_STORAGE",
        ])
    except Exception:
        pass


PICK_CODE = 7701
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/gif": ".gif", "image/bmp": ".bmp", "video/mp4": ".mp4",
            "video/x-matroska": ".mkv", "video/3gpp": ".3gp", "video/quicktime": ".mov",
            "video/x-msvideo": ".avi", "video/webm": ".webm"}


def open_gallery_picker(on_result):
    """Phone ka apna Gallery/Photo picker kholta hai (storage permission ki zarurat nahi).
    Chalu ho gaya to True, nahi to False (tab purana folder browser chalega).
    on_result(list_of_uri_strings) jab user select karke wapas aaye."""
    try:
        from android import activity
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        Build = autoclass("android.os.Build$VERSION")
        act = autoclass("org.kivy.android.PythonActivity").mActivity

        if Build.SDK_INT >= 33:
            MediaStore = autoclass("android.provider.MediaStore")
            it = Intent("android.provider.action.PICK_IMAGES")
            it.putExtra("android.provider.extra.PICK_IMAGES_MAX",
                        int(MediaStore.getPickImagesMaxLimit()))
        else:
            it = Intent(Intent.ACTION_GET_CONTENT)
            it.setType("*/*")
            it.addCategory(Intent.CATEGORY_OPENABLE)
            it.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, True)
            try:
                it.putExtra(Intent.EXTRA_MIME_TYPES, ["image/*", "video/*"])
            except Exception:
                it.setType("image/*")

        def cb(req, res, data):
            if req != PICK_CODE:
                return
            try:
                activity.unbind(on_activity_result=cb)
            except Exception:
                pass
            uris = []
            try:
                if res == -1 and data is not None:  # RESULT_OK
                    clip = data.getClipData()
                    if clip is not None:
                        for i in range(clip.getItemCount()):
                            uris.append(str(clip.getItemAt(i).getUri().toString()))
                    elif data.getData() is not None:
                        uris.append(str(data.getData().toString()))
            except Exception:
                uris = []
            on_result(uris)

        activity.bind(on_activity_result=cb)
        act.startActivityForResult(it, PICK_CODE)
        return True
    except Exception:
        return False


def copy_uri_to_file(uri_str, dst_dir, i):
    """Gallery wali file ko app ke temp folder mein copy karta hai, path return karta hai."""
    from jnius import autoclass
    Uri = autoclass("android.net.Uri")
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    cr = act.getContentResolver()
    uri = Uri.parse(uri_str)
    mime = str(cr.getType(uri) or "").lower()
    ext = MIME_EXT.get(mime) or (".mp4" if mime.startswith("video") else ".jpg")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, f"pick_{int(time.time() * 1000)}_{i}{ext}")
    pfd = cr.openFileDescriptor(uri, "r")
    fd = pfd.detachFd()
    try:
        with os.fdopen(fd, "rb") as fi, open(dst, "wb") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
    except Exception:
        _rm(dst)
        raise
    return dst


BACKUP_CODE, OPEN_CODE = 7702, 7703


def android_document(action, code, on_result, filename=None):
    """Android ka file-picker kholta hai. action: 'create' (save karna) ya 'open' (file chunna).
    on_result(uri_string ya None). Chalu ho gaya to True, warna False."""
    try:
        from android import activity
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        if action == "create":
            it = Intent(Intent.ACTION_CREATE_DOCUMENT)
            it.setType("application/zip")
            it.putExtra(Intent.EXTRA_TITLE, filename or "VaultBackup.zip")
        else:
            it = Intent(Intent.ACTION_OPEN_DOCUMENT)
            it.setType("*/*")
        it.addCategory(Intent.CATEGORY_OPENABLE)

        def cb(req, res, data):
            if req != code:
                return
            try:
                activity.unbind(on_activity_result=cb)
            except Exception:
                pass
            uri = None
            try:
                if res == -1 and data is not None and data.getData() is not None:
                    uri = str(data.getData().toString())
            except Exception:
                uri = None
            on_result(uri)

        activity.bind(on_activity_result=cb)
        act.startActivityForResult(it, code)
        return True
    except Exception:
        return False


def copy_path_to_uri(src, uri_str):
    """App ki file ko user ke chune hue Android document (Drive/Downloads) mein likhta hai."""
    from jnius import autoclass
    Uri = autoclass("android.net.Uri")
    cr = autoclass("org.kivy.android.PythonActivity").mActivity.getContentResolver()
    pfd = cr.openFileDescriptor(Uri.parse(uri_str), "w")
    fd = pfd.detachFd()
    with os.fdopen(fd, "wb") as fo, open(src, "rb") as fi:
        shutil.copyfileobj(fi, fo, 1 << 20)


def copy_uri_to_path(uri_str, dst):
    """User ke chune hue Android document ko app ke temp path par copy karta hai."""
    from jnius import autoclass
    Uri = autoclass("android.net.Uri")
    cr = autoclass("org.kivy.android.PythonActivity").mActivity.getContentResolver()
    pfd = cr.openFileDescriptor(Uri.parse(uri_str), "r")
    fd = pfd.detachFd()
    try:
        with os.fdopen(fd, "rb") as fi, open(dst, "wb") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
    except Exception:
        _rm(dst)
        raise


def export_to_gallery(src, fname):
    """Decrypted file ko Gallery mein wapas rakhta hai. Pehle Android MediaStore (Android 10+),
    na chale to seedha folder (Pictures/VaultRestored). Jagah ka naam return karta hai."""
    try:
        from jnius import autoclass
        Build = autoclass("android.os.Build$VERSION")
        if Build.SDK_INT >= 29:
            ContentValues = autoclass("android.content.ContentValues")
            cr = autoclass("org.kivy.android.PythonActivity").mActivity.getContentResolver()
            vid = is_video(fname)
            mime = mimetypes.guess_type(fname)[0] or ("video/mp4" if vid else "image/jpeg")
            rel = ("Movies" if vid else "Pictures") + "/VaultRestored"
            vals = ContentValues()
            vals.put("_display_name", fname)
            vals.put("mime_type", mime)
            vals.put("relative_path", rel)
            coll = autoclass("android.provider.MediaStore$Video$Media" if vid
                             else "android.provider.MediaStore$Images$Media").EXTERNAL_CONTENT_URI
            uri = cr.insert(coll, vals)
            if uri is None:
                raise Exception("MediaStore insert nahi hua")
            pfd = cr.openFileDescriptor(uri, "w")
            fd = pfd.detachFd()
            with os.fdopen(fd, "wb") as fo, open(src, "rb") as fi:
                shutil.copyfileobj(fi, fo, 1 << 20)
            return rel + "/" + fname
    except Exception:
        pass
    os.makedirs(RESTORE_DIR, exist_ok=True)
    stem, ext = os.path.splitext(fname)
    dest = os.path.join(RESTORE_DIR, stem + ext)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(RESTORE_DIR, f"{stem}_{n}{ext}")
        n += 1
    shutil.copyfile(src, dest)
    return dest


def base_name(n):
    return n[:-6] if n.endswith(".vault") else n


def is_video(name):
    return base_name(name).lower().endswith(VID_EXTS)


# ---------------- Crypto helpers ----------------
def _ks(key, nonce, idx, n):
    return hashlib.shake_256(key + nonce + idx.to_bytes(8, "big")).digest(n)


def _xor(data, ks):
    n = len(data)
    if n == 0:
        return b""
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(n, "big")


def _xor_bytes(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def _rm(path):
    try:
        os.remove(path)
    except Exception:
        pass


# ---------------- Storage ----------------
class Store:
    def __init__(self, base, cache):
        self.media = os.path.join(base, "media")
        self.cache = cache
        os.makedirs(self.media, exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        self.key = None
        self.clear_cache()
        self.cfg_path = os.path.join(base, "config.json")
        self.cfg = {"pin": None, "albums": {}, "autolock": 60, "fails": 0, "lock_until": 0}
        try:
            with open(self.cfg_path) as f:
                self.cfg.update(json.load(f))
        except Exception:
            pass

    def save(self):
        tmp = self.cfg_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.cfg, f)
        os.replace(tmp, self.cfg_path)

    # ----- keys / PIN -----
    @staticmethod
    def h(p):
        return hashlib.sha256(p.encode()).hexdigest()

    @staticmethod
    def _pk(pin, salt):
        return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 50000, 32)

    @staticmethod
    def _mask(pk):
        return hashlib.shake_256(pk + b"wrap").digest(32)

    def has_keys(self):
        return "wrapped" in self.cfg

    def is_legacy(self):
        return bool(self.cfg.get("pin")) and not self.has_keys()

    def check_legacy(self, pin):
        return self.cfg.get("pin") == self.h(pin)

    def setup_keys(self, pin, master=None):
        master = master or os.urandom(32)
        salt = os.urandom(16)
        pk = self._pk(pin, salt)
        self.cfg["salt"] = salt.hex()
        self.cfg["wrapped"] = _xor_bytes(master, self._mask(pk)).hex()
        self.cfg["check"] = hashlib.sha256(master).hexdigest()
        self.cfg.pop("pin", None)
        self.save()
        self.key = master

    def unlock(self, pin):
        if not self.has_keys():
            return None
        pk = self._pk(pin, bytes.fromhex(self.cfg["salt"]))
        master = _xor_bytes(bytes.fromhex(self.cfg["wrapped"]), self._mask(pk))
        if hashlib.sha256(master).hexdigest() == self.cfg["check"]:
            return master
        return None

    def rewrap(self, master, newpin):
        salt = os.urandom(16)
        pk = self._pk(newpin, salt)
        self.cfg["salt"] = salt.hex()
        self.cfg["wrapped"] = _xor_bytes(master, self._mask(pk)).hex()
        self.save()

    # ----- recovery key / backup / import -----
    def recovery_key(self):
        """Master key ko padhne layak code mein deta hai (vault khula hona chahiye)."""
        if self.key is None:
            raise Exception("Vault locked")
        s = base64.b32encode(self.key).decode().rstrip("=")
        return "-".join(s[i:i + 4] for i in range(0, len(s), 4))

    @staticmethod
    def parse_recovery_key(text):
        s = "".join(c for c in (text or "").upper() if c.isalnum())
        try:
            s += "=" * (-len(s) % 8)
            m = base64.b32decode(s)
        except Exception:
            return None
        return m if len(m) == 32 else None

    def master_ok(self, master, cfg=None):
        cfg = cfg if cfg is not None else self.cfg
        return bool(master) and "check" in cfg and \
            hashlib.sha256(master).hexdigest() == cfg["check"]

    def recover_with_key(self, master, newpin):
        """PIN bhool gaye: Recovery Key se vault kholkar naya PIN set karta hai."""
        if not self.has_keys() or not self.master_ok(master):
            return False
        self.rewrap(master, newpin)
        self.key = master
        self.cfg["fails"], self.cfg["lock_until"] = 0, 0
        self.save()
        return True

    def make_backup(self, dest, cb=None):
        """Encrypted files + albums ki ek zip banata hai. PIN wali key zip mein NAHI jaati,
        isliye zip sirf Recovery Key se hi khul sakti hai."""
        names = [f for f in os.listdir(self.media) if f.endswith(".vault")]
        meta = {"v": 1, "check": self.cfg.get("check"),
                "albums": self.cfg.get("albums", {}),
                "autolock": self.cfg.get("autolock", 60)}
        tmp = dest + ".part"
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                z.writestr("config.json", json.dumps(meta))
                for i, n in enumerate(names):
                    z.write(os.path.join(self.media, n), "media/" + n)
                    if cb:
                        cb(i + 1, len(names))
            os.replace(tmp, dest)
        except Exception:
            _rm(tmp)
            raise
        return len(names)

    @staticmethod
    def read_backup(zpath):
        try:
            with zipfile.ZipFile(zpath) as z:
                meta = json.loads(z.read("config.json").decode())
                names = [n[6:] for n in z.namelist()
                         if n.startswith("media/") and n.endswith(".vault")]
            if not isinstance(meta, dict) or "check" not in meta:
                raise ValueError("no check")
        except Exception:
            raise Exception("Ye Vault ka backup nahi hai")
        return meta, names

    def import_backup(self, zpath, master, newpin, cb=None):
        """Backup zip ko khali vault mein wapas laata hai, naye PIN ke saath."""
        meta, names = self.read_backup(zpath)
        if not self.master_ok(master, meta):
            raise Exception("Recovery Key galat hai")
        if self.media_list() or self.plain_files():
            raise Exception("Import sirf khali vault mein ho sakta hai")
        copied = []
        try:
            with zipfile.ZipFile(zpath) as z:
                for i, n in enumerate(names):
                    if os.path.basename(n) != n or n.startswith("."):
                        continue
                    dst = os.path.join(self.media, n)
                    tmp = dst + ".part"
                    try:
                        with z.open("media/" + n) as fi, open(tmp, "wb") as fo:
                            shutil.copyfileobj(fi, fo, 1 << 20)
                        os.replace(tmp, dst)
                    except Exception:
                        _rm(tmp)
                        raise
                    copied.append(dst)
                    if cb:
                        cb(i + 1, len(names))
        except Exception:
            for d in copied:
                _rm(d)
            raise
        have = set(os.listdir(self.media))
        albums = meta.get("albums", {})
        self.cfg["albums"] = {a: [x for x in lst if x in have]
                              for a, lst in albums.items() if isinstance(lst, list)}
        self.cfg["autolock"] = meta.get("autolock", 60)
        self.cfg["fails"], self.cfg["lock_until"] = 0, 0
        self.setup_keys(newpin, master)
        return len(copied)

    # ----- encrypt / decrypt -----
    def encrypt_file(self, src, dst, key=None):
        key = key or self.key
        if key is None:
            raise Exception("Vault locked")
        nonce = os.urandom(16)
        tmp = dst + ".part"
        try:
            with open(src, "rb") as fi, open(tmp, "wb") as fo:
                fo.write(MAGIC + nonce)
                idx = 0
                while True:
                    b = fi.read(CHUNK)
                    if not b:
                        break
                    fo.write(_xor(b, _ks(key, nonce, idx, len(b))))
                    idx += 1
            os.replace(tmp, dst)
        except Exception:
            _rm(tmp)
            raise

    def decrypt_file(self, src, dst, key=None):
        key = key or self.key
        if key is None:
            raise Exception("Vault locked")
        try:
            with open(src, "rb") as fi, open(dst, "wb") as fo:
                head = fi.read(20)
                if head[:4] != MAGIC:
                    raise Exception("File format galat")
                nonce = head[4:]
                idx = 0
                while True:
                    b = fi.read(CHUNK)
                    if not b:
                        break
                    fo.write(_xor(b, _ks(key, nonce, idx, len(b))))
                    idx += 1
        except Exception:
            _rm(dst)
            raise

    def cache_path(self, name):
        return os.path.join(self.cache, base_name(name))

    def thumb_path(self, name):
        return os.path.join(self.cache, "t_" + base_name(name) + ".jpg")

    def decrypt_cached(self, name, key):
        if key is None:
            raise Exception("Vault locked")
        path = self.cache_path(name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        tmp = path + f".{threading.get_ident()}.tmp"
        self.decrypt_file(os.path.join(self.media, name), tmp, key)
        os.replace(tmp, path)
        return path

    def thumb_cached(self, name, key):
        """Chhota JPEG thumbnail banata hai (PIL ho to), warna poori decrypted file."""
        if key is None:
            raise Exception("Vault locked")
        tp = self.thumb_path(name)
        if os.path.exists(tp) and os.path.getsize(tp) > 0:
            return tp
        full = self.cache_path(name)
        tmp = None
        if os.path.exists(full) and os.path.getsize(full) > 0:
            src = full
        else:
            tmp = full + f".{threading.get_ident()}.tmp"
            self.decrypt_file(os.path.join(self.media, name), tmp, key)
            src = tmp
        result = None
        if PILImage is not None:
            part = tp + f".{threading.get_ident()}.part"
            try:
                im = PILImage.open(src)
                try:
                    im.draft("RGB", (512, 512))
                except Exception:
                    pass
                im = im.convert("RGB")
                im.thumbnail((360, 360))
                im.save(part, "JPEG", quality=80)
                os.replace(part, tp)
                result = tp
            except Exception:
                result = None
            finally:
                _rm(part)
        if result is None:
            if tmp:
                os.replace(tmp, full)
                tmp = None
            result = full
        if tmp:
            _rm(tmp)
        return result

    def restore_file(self, name, key):
        fname = base_name(name)
        tmp = os.path.join(self.cache, f"restore_{threading.get_ident()}_{fname}")
        self.decrypt_file(os.path.join(self.media, name), tmp, key)
        try:
            return export_to_gallery(tmp, fname)
        finally:
            _rm(tmp)

    def clear_cache(self):
        try:
            for f in os.listdir(self.cache):
                _rm(os.path.join(self.cache, f))
        except Exception:
            pass

    # ----- media -----
    def media_list(self, kind="all"):
        files = [f for f in os.listdir(self.media)
                 if f.endswith(".vault") and base_name(f).lower().endswith(ALL_EXTS)]
        if kind == "photo":
            files = [f for f in files if not is_video(f)]
        elif kind == "video":
            files = [f for f in files if is_video(f)]
        return sorted(files, reverse=True)

    def album_items(self, album, kind="all"):
        inside = set(self.cfg["albums"].get(album, []))
        return [n for n in self.media_list(kind) if n in inside]

    def plain_files(self):
        return [f for f in os.listdir(self.media) if f.lower().endswith(ALL_EXTS)]

    def migrate_plain(self, cb=None):
        files = self.plain_files()
        done = 0
        for i, f in enumerate(files):
            src = os.path.join(self.media, f)
            try:
                self.encrypt_file(src, src + ".vault")
                os.remove(src)
                for lst in self.cfg["albums"].values():
                    if f in lst:
                        lst[lst.index(f)] = f + ".vault"
                done += 1
            except Exception:
                pass
            if cb:
                cb(i + 1, len(files))
        self.save()
        return done

    def add_one(self, path, i, move=False, key=None):
        """Success par None, fail par error text return karta hai."""
        try:
            ext = os.path.splitext(path)[1].lower()
            name = f"{int(time.time() * 1000)}_{i}{ext}.vault"
            self.encrypt_file(path, os.path.join(self.media, name), key)
            if move:
                _rm(path)
            return None
        except Exception as e:
            return str(e)

    def delete(self, name):
        for p in (os.path.join(self.media, name), self.cache_path(name), self.thumb_path(name)):
            _rm(p)
        for a in self.cfg["albums"].values():
            if name in a:
                a.remove(name)
        self.save()

    def create_album(self, name):
        if name and name not in self.cfg["albums"]:
            self.cfg["albums"][name] = []
            self.save()
            return True
        return False

    def delete_album(self, name):
        self.cfg["albums"].pop(name, None)
        self.save()

    def add_to_album(self, album, name):
        lst = self.cfg["albums"].setdefault(album, [])
        if name not in lst:
            lst.append(name)
        self.save()


# ---------------- Premium UI widgets ----------------
class RBtn(Button):
    """Rounded, flat, modern button."""

    def __init__(self, bg=CARD, radius=12, **kw):
        kw.setdefault("bold", True)
        super().__init__(**kw)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.color = (1, 1, 1, 1)
        self._rgba = hx(bg)
        with self.canvas.before:
            self._col = Color(*self._rgba)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._sync, size=self._sync, state=self._paint)

    def _sync(self, *a):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _paint(self, *a):
        r, g, b, al = self._rgba
        k = .72 if self.state == "down" else 1
        self._col.rgba = (r * k, g * k, b * k, al)

    def set_bg(self, color):
        self._rgba = hx(color)
        self._paint()


class Cover(AsyncImage):
    """Image jiske peeche rounded placeholder card hota hai."""

    def __init__(self, **kw):
        super().__init__(**kw)
        if hasattr(Image, "fit_mode"):
            self.fit_mode = "cover"
        else:
            self.allow_stretch = True
            self.keep_ratio = False
        with self.canvas.before:
            Color(*hx(CARD))
            self._ph = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
        self.color = (1, 1, 1, 0)
        self.bind(pos=self._sync, size=self._sync, texture=self._tex)

    def _tex(self, *a):
        self.color = (1, 1, 1, 1 if self.texture else 0)

    def _sync(self, *a):
        self._ph.pos = self.pos
        self._ph.size = self.size


class Thumb(ButtonBehavior, Cover):
    pass


class AlbumCard(ButtonBehavior, BoxLayout):
    def __init__(self, title, count, **kw):
        super().__init__(orientation="vertical", spacing=dp(4), **kw)
        self.cover = Cover()
        self.add_widget(self.cover)
        name = Label(text=title, bold=True, font_size=dp(15), size_hint_y=None,
                     height=dp(22), halign="left", shorten=True)
        name.bind(size=lambda w, s: setattr(w, "text_size", s))
        cnt = Label(text=f"{count} items", font_size=dp(12), color=hx(MUTED),
                    size_hint_y=None, height=dp(20), halign="left")
        cnt.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.add_widget(name)
        self.add_widget(cnt)


class PinDots(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.n = 0
        self.bind(pos=self.draw, size=self.draw)

    def set(self, n):
        self.n = n
        self.draw()

    def draw(self, *a):
        self.canvas.clear()
        d, gap = dp(16), dp(18)
        x0 = self.center_x - (4 * d + 3 * gap) / 2
        y = self.center_y - d / 2
        with self.canvas:
            Color(*hx(ACCENT))
            for i in range(4):
                x = x0 + i * (d + gap)
                if i < self.n:
                    Ellipse(pos=(x, y), size=(d, d))
                else:
                    Line(circle=(x + d / 2, y + d / 2, d / 2), width=dp(1.5))


class SwipeStage(BoxLayout):
    """Left/right swipe pakadta hai (viewer mein next/previous ke liye)."""

    def __init__(self, swipe_cb, **kw):
        super().__init__(**kw)
        self.swipe_cb = swipe_cb
        self._p = None

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self._p = (touch.x, touch.y, touch.uid)
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        p = self._p
        if p and p[2] == touch.uid:
            self._p = None
            dx, dy = touch.x - p[0], touch.y - p[1]
            if abs(dx) > dp(70) and abs(dx) > abs(dy) * 1.5:
                self.swipe_cb(-1 if dx > 0 else 1)
        return super().on_touch_up(touch)


# ---------------- UI helpers ----------------
def btn(text, cb, color=CARD, **kw):
    b = RBtn(text=text, bg=color, **kw)
    b.bind(on_release=lambda *a: cb())
    return b


def title_label(text, size=24):
    lbl = Label(text=text, font_size=dp(size), bold=True, halign="left", shorten=True)
    lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
    return lbl


def header(title_widget, buttons, h=56):
    bar = BoxLayout(size_hint_y=None, height=dp(h), spacing=dp(6), padding=(dp(10), dp(6)))
    bar.add_widget(title_widget)
    for b in buttons:
        bar.add_widget(b)
    return bar


def nav_bar(active):
    app = App.get_running_app()
    bar = BoxLayout(size_hint_y=None, height=dp(62), padding=(dp(8), dp(8)), spacing=dp(6))
    with bar.canvas.before:
        Color(*hx(NAVBG))
        rect = Rectangle(pos=bar.pos, size=bar.size)
    bar.bind(pos=lambda w, v: setattr(rect, "pos", v),
             size=lambda w, v: setattr(rect, "size", v))
    tabs = [("Gallery", "gallery", app.show_gallery),
            ("Albums", "albums", lambda: app.go("albums")),
            ("Import", "import", lambda: app.go("import")),
            ("Settings", "settings", lambda: app.go("settings"))]
    for label, key, cb in tabs:
        bar.add_widget(btn(label, cb, ACCENT if key == active else NAVBG,
                           font_size=dp(13), radius=14))
    return bar


def popup(title, content, size_hint, auto_dismiss=True):
    return Popup(title=title, content=content, size_hint=size_hint,
                 auto_dismiss=auto_dismiss, background="",
                 background_color=hx(CARD), separator_color=hx(ACCENT))


def info(title, text, h=.3):
    lbl = Label(text=text, halign="center")
    lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
    pop = popup(title, lbl, (.88, h))
    pop.open()
    return pop


def ask(title, text, yes_cb):
    box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
    lbl = Label(text=text, halign="center")
    lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
    box.add_widget(lbl)
    row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(48))
    pop = popup(title, box, (.85, .4))
    row.add_widget(btn("Cancel", pop.dismiss, SOFT))
    row.add_widget(btn("Yes", lambda: (pop.dismiss(), yes_cb()), RED))
    box.add_widget(row)
    pop.open()


def new_album_popup(after):
    app = App.get_running_app()
    box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
    ti = TextInput(hint_text="Album name", multiline=False, size_hint_y=None, height=dp(46))
    box.add_widget(ti)
    pop = popup("New Album", box, (.85, .32))

    def ok():
        n = ti.text.strip()
        if n and app.store.create_album(n):
            pop.dismiss()
            after(n)
        else:
            ti.text = ""
            ti.hint_text = "Naam khali ya already hai"
    box.add_widget(btn("Create", ok, ACCENT, size_hint_y=None, height=dp(46)))
    pop.open()


def recovery_popup(after):
    """PIN bhool gaye: Recovery Key + naya PIN."""
    app = App.get_running_app()
    st = app.store
    box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
    k = TextInput(hint_text="Recovery Key paste karein", multiline=False,
                  size_hint_y=None, height=dp(46))
    p = TextInput(hint_text="Naya 4-digit PIN", multiline=False, input_filter="int",
                  password=True, size_hint_y=None, height=dp(46))
    msg = Label(color=hx("#FF6B6B"), size_hint_y=None, height=dp(24), font_size=dp(13))
    for w in (k, p, msg):
        box.add_widget(w)
    pop = popup("Recovery Key se kholein", box, (.92, .5))

    def ok():
        pin = p.text.strip()
        if len(pin) != 4 or not pin.isdigit():
            msg.text = "PIN 4 ank ka hona chahiye"
            return
        m = st.parse_recovery_key(k.text)
        if m is None or not st.recover_with_key(m, pin):
            msg.text = "Recovery Key galat hai"
            return
        pop.dismiss()
        after()
    row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(48))
    row.add_widget(btn("Cancel", pop.dismiss, SOFT))
    row.add_widget(btn("Kholein", ok, GREEN))
    box.add_widget(row)
    pop.open()


# ---------------- PIN keypad widget ----------------
class PinPad(BoxLayout):
    def __init__(self, on_pin, **kw):
        super().__init__(orientation="vertical", padding=dp(30), spacing=dp(12), **kw)
        self.on_pin = on_pin
        self.buf = ""
        self.title = Label(font_size=dp(22), bold=True, size_hint_y=.15)
        self.dots = PinDots(size_hint_y=.1)
        self.msg = Label(color=hx("#FF6B6B"), size_hint_y=.08)
        grid = GridLayout(cols=3, spacing=dp(12))
        for k in ["1", "2", "3", "4", "5", "6", "7", "8", "9", "C", "0", "<"]:
            b = RBtn(text=k, font_size=dp(26), bg=CARD, radius=30)
            b.bind(on_release=lambda x, k=k: self.press(k))
            grid.add_widget(b)
        for w in (self.title, self.dots, self.msg, grid):
            self.add_widget(w)
        self.update()

    def update(self):
        self.dots.set(len(self.buf))

    def reset(self, title=None, msg=""):
        self.buf = ""
        if title:
            self.title.text = title
        self.msg.text = msg
        self.update()

    def press(self, k):
        self.msg.text = ""
        if k == "C":
            self.buf = ""
        elif k == "<":
            self.buf = self.buf[:-1]
        elif len(self.buf) < 4:
            self.buf += k
        self.update()
        if len(self.buf) == 4:
            Clock.schedule_once(self._fire, .15)

    def _fire(self, dt):
        if len(self.buf) != 4:
            return
        pin = self.buf
        self.buf = ""
        self.update()
        self.on_pin(pin)


# ---------------- PIN Screen ----------------
class PinScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.mode, self.first = "unlock", ""
        self.pad = PinPad(self.on_pin)
        root = BoxLayout(orientation="vertical", padding=(0, 0, 0, dp(8)))
        root.add_widget(self.pad)
        bx = BoxLayout(size_hint_y=None, height=dp(48), padding=(dp(20), 0))
        bx.add_widget(btn("PIN bhool gaye? Recovery Key", self.forgot, SOFT, font_size=dp(13)))
        root.add_widget(bx)
        self.add_widget(root)

    def forgot(self):
        app = App.get_running_app()
        if not app.store.has_keys():
            info("Recovery Key", "Abhi vault ki key nahi bani hai.\nPehle PIN banayein.", .3)
            return
        recovery_popup(app.after_unlock)

    def on_pre_enter(self, *a):
        st = App.get_running_app().store
        self.first = ""
        if st.has_keys():
            self.mode, title = "unlock", "Enter PIN"
        elif st.is_legacy():
            self.mode, title = "legacy", "Enter PIN"
        else:
            self.mode, title = "create", "Create 4-digit PIN"
        self.pad.reset(title)

    def on_pin(self, pin):
        app = App.get_running_app()
        st = app.store
        if self.mode in ("unlock", "legacy"):
            lu = st.cfg.get("lock_until", 0)
            if lu > time.time():
                self.pad.msg.text = f"Bahut galat try. {int(lu - time.time()) + 1} sec baad try karein"
                return
            if self.mode == "unlock":
                m = st.unlock(pin)
                ok = m is not None
                if ok:
                    st.key = m
            else:
                ok = st.check_legacy(pin)
                if ok:
                    st.setup_keys(pin)
            if ok:
                st.cfg["fails"], st.cfg["lock_until"] = 0, 0
                st.save()
                app.after_unlock()
            else:
                f = st.cfg.get("fails", 0) + 1
                st.cfg["fails"] = f
                msg = "Wrong PIN!"
                if f % 5 == 0:
                    wait = 30 * (f // 5)
                    st.cfg["lock_until"] = time.time() + wait
                    msg = f"{f} galat try. {wait} sec ruko"
                st.save()
                self.pad.msg.text = msg
        elif self.mode == "create":
            self.first, self.mode = pin, "confirm"
            self.pad.reset("Confirm PIN")
        else:
            if pin == self.first:
                st.setup_keys(pin)
                app.after_unlock()
            else:
                self.mode = "create"
                self.pad.reset("Create 4-digit PIN", "PIN match nahi hua, dobara try karein")


# ---------------- Change PIN Screen ----------------
class ChangePinScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        self.step, self.master, self.first = "old", None, ""
        root = BoxLayout(orientation="vertical", padding=(0, 0, 0, dp(8)))
        self.pad = PinPad(self.on_pin)
        root.add_widget(self.pad)
        bx = BoxLayout(size_hint_y=None, height=dp(48), padding=(dp(20), 0))
        bx.add_widget(btn("Cancel", lambda: app.go("settings"), SOFT))
        root.add_widget(bx)
        self.add_widget(root)

    def on_pre_enter(self, *a):
        self.step, self.master, self.first = "old", None, ""
        self.pad.reset("Purana PIN daalein")

    def on_pin(self, pin):
        app = App.get_running_app()
        st = app.store
        if self.step == "old":
            m = st.unlock(pin)
            if m is None:
                self.pad.reset(msg="Wrong PIN!")
            else:
                self.master, self.step = m, "new"
                self.pad.reset("Naya PIN daalein")
        elif self.step == "new":
            self.first, self.step = pin, "confirm"
            self.pad.reset("Naya PIN confirm karein")
        else:
            if pin == self.first:
                st.rewrap(self.master, pin)
                info("Done", "PIN badal gaya!")
                app.go("settings")
            else:
                self.step = "new"
                self.pad.reset("Naya PIN daalein", "PIN match nahi hua")


# ---------------- Settings Screen ----------------
class SettingsScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        root = BoxLayout(orientation="vertical")
        root.add_widget(header(title_label("Settings"), [], 64))
        body = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        lbl = Label(text="Auto-lock (koi touch na ho to)", size_hint_y=None,
                    height=dp(26), color=hx(MUTED), halign="left")
        lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
        body.add_widget(lbl)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.abtns = {}
        for label, sec in AUTOLOCK_OPTS:
            b = btn(label, lambda s=sec: self.set_auto(s), font_size=dp(13), radius=18)
            self.abtns[sec] = b
            row.add_widget(b)
        body.add_widget(row)
        body.add_widget(btn("Change PIN", lambda: app.go("changepin"), ACCENT,
                            size_hint_y=None, height=dp(48)))
        body.add_widget(btn("Recovery Key dikhayein", self.show_key, GREEN,
                            size_hint_y=None, height=dp(48)))
        body.add_widget(btn("Backup banayein (Drive/Downloads)", self.backup, SOFT,
                            size_hint_y=None, height=dp(48)))
        body.add_widget(btn("Backup se Import karein", self.import_backup, SOFT,
                            size_hint_y=None, height=dp(48)))
        note = Label(
            text=("Dhyan dein:\n"
                  "- Recovery Key apne Gmail mein save kar lein. Iske bina PIN bhoolne par "
                  "ya app uninstall hone par files wapas nahi aa sakti.\n"
                  "- Backup file Drive mein rakhein. Wo sirf Recovery Key se khulti hai.\n"
                  "- Restore ki hui files Gallery ke VaultRestored folder mein aati hain."),
            halign="left", valign="top", color=hx(MUTED), font_size=dp(13))
        note.bind(size=lambda w, s: setattr(w, "text_size", (s[0], s[1])))
        body.add_widget(note)
        root.add_widget(body)
        root.add_widget(nav_bar("settings"))
        self.add_widget(root)

    def on_pre_enter(self, *a):
        cur = App.get_running_app().store.cfg.get("autolock", 60)
        for sec, b in self.abtns.items():
            b.set_bg(ACCENT if sec == cur else CARD)

    # ----- Recovery Key / Backup / Import -----
    def show_key(self):
        app = App.get_running_app()
        try:
            key = app.store.recovery_key()
        except Exception as e:
            info("Recovery Key", str(e), .3)
            return
        parts = key.split("-")
        text = "\n".join("  ".join(parts[i:i + 3]) for i in range(0, len(parts), 3))
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        lbl = Label(text=text, font_size=dp(17), halign="center")
        lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        box.add_widget(lbl)
        note = Label(text="Ise Gmail mein khud ko bhej kar ya Notes mein save karein.\n"
                          "Kisi ko mat dein: isse poora vault khul sakta hai.",
                     halign="center", color=hx(MUTED), font_size=dp(13),
                     size_hint_y=None, height=dp(60))
        note.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        box.add_widget(note)
        pop = popup("Recovery Key", box, (.94, .7))
        row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(48))
        holder = {}

        def do_copy():
            try:
                from kivy.core.clipboard import Clipboard
                Clipboard.copy(key)
                holder["b"].text = "Copy ho gaya!"
            except Exception:
                holder["b"].text = "Copy nahi hua"

        holder["b"] = btn("Copy", do_copy, ACCENT)
        row.add_widget(holder["b"])
        row.add_widget(btn("Band karein", pop.dismiss, SOFT))
        box.add_widget(row)
        pop.open()

    def backup(self):
        ask("Backup banayein",
            "Backup file Recovery Key ke bina khul nahi sakti.\n"
            "Pehle 'Recovery Key dikhayein' se key save kar lein.\n\nAage badhein?",
            self._pick_backup_dest)

    def _pick_backup_dest(self):
        app = App.get_running_app()
        fname = "VaultBackup_" + time.strftime("%Y%m%d") + ".zip"
        app.busy = True  # picker khulne par app auto-lock na ho

        def got(uri):
            Clock.schedule_once(lambda dt: self._do_backup(uri))

        if not android_document("create", BACKUP_CODE, got, fname):
            app.busy = False
            info("Backup", "Ye option sirf Android APK mein chalta hai.", .3)

    def _do_backup(self, uri):
        app = App.get_running_app()
        app.last = time.time()
        if not uri:
            app.busy = False
            return
        lbl = Label(text="Backup ban raha hai...", halign="center")
        pop = popup("Backup", lbl, (.8, .25), auto_dismiss=False)
        pop.open()
        tmp = os.path.join(app.user_data_dir, "backup_tmp.zip")

        def prog(i, n):
            Clock.schedule_once(lambda dt: setattr(lbl, "text", f"Backup {i}/{n}"))

        def done(n, err):
            pop.dismiss()
            app.busy = False
            app.last = time.time()
            if err:
                info("Backup fail", err[:150], .4)
            elif n == 0:
                info("Backup", "Vault abhi khali hai, backup mein koi file nahi gayi.", .3)
            else:
                info("Backup ho gaya", f"{n} files ka backup save ho gaya.\n"
                     "Isse Google Drive mein rakhein, aur Recovery Key alag jagah.", .4)

        def work():
            err, n = None, 0
            try:
                n = app.store.make_backup(tmp, prog)
                Clock.schedule_once(lambda dt: setattr(lbl, "text", "Save ho raha hai..."))
                copy_path_to_uri(tmp, uri)
            except Exception as e:
                err = str(e)
            _rm(tmp)
            Clock.schedule_once(lambda dt: done(n, err))

        threading.Thread(target=work, daemon=True).start()

    def import_backup(self):
        app = App.get_running_app()
        if app.store.media_list():
            info("Import", "Import sirf khali vault mein ho sakta hai.\n"
                 "App nayi install karke PIN banayein, phir yahan import karein.", .4)
            return
        app.busy = True

        def got(uri):
            Clock.schedule_once(lambda dt: self._got_backup(uri))

        if not android_document("open", OPEN_CODE, got):
            app.busy = False
            info("Import", "Ye option sirf Android APK mein chalta hai.", .3)

    def _got_backup(self, uri):
        app = App.get_running_app()
        app.last = time.time()
        if not uri:
            app.busy = False
            return
        lbl = Label(text="Backup file padh raha hoon...", halign="center")
        pop = popup("Import", lbl, (.8, .25), auto_dismiss=False)
        pop.open()
        tmp = os.path.join(app.user_data_dir, "import_tmp.zip")

        def fail(err):
            pop.dismiss()
            app.busy = False
            _rm(tmp)
            info("Import fail", err[:150], .4)

        def ready(n):
            pop.dismiss()
            self._ask_key(tmp, n)

        def work():
            try:
                copy_uri_to_path(uri, tmp)
                meta, names = app.store.read_backup(tmp)
            except Exception as e:
                msg = str(e)
                Clock.schedule_once(lambda dt: fail(msg))
                return
            Clock.schedule_once(lambda dt: ready(len(names)))

        threading.Thread(target=work, daemon=True).start()

    def _ask_key(self, tmp, count):
        app = App.get_running_app()
        st = app.store
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        box.add_widget(Label(text=f"Backup mein {count} files hain.", size_hint_y=None,
                             height=dp(24), font_size=dp(14)))
        k = TextInput(hint_text="Recovery Key paste karein", multiline=False,
                      size_hint_y=None, height=dp(46))
        p = TextInput(hint_text="Naya 4-digit PIN", multiline=False, input_filter="int",
                      password=True, size_hint_y=None, height=dp(46))
        msg = Label(color=hx("#FF6B6B"), size_hint_y=None, height=dp(24), font_size=dp(13))
        for w in (k, p, msg):
            box.add_widget(w)
        pop = popup("Backup Import", box, (.92, .55), auto_dismiss=False)

        def cancel():
            pop.dismiss()
            app.busy = False
            _rm(tmp)

        def go():
            pin = p.text.strip()
            if len(pin) != 4 or not pin.isdigit():
                msg.text = "PIN 4 ank ka hona chahiye"
                return
            m = st.parse_recovery_key(k.text)
            if m is None:
                msg.text = "Recovery Key galat hai"
                return
            pop.dismiss()
            self._run_import(tmp, m, pin)

        row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(48))
        row.add_widget(btn("Cancel", cancel, SOFT))
        row.add_widget(btn("Import", go, GREEN))
        box.add_widget(row)
        pop.open()

    def _run_import(self, tmp, master, pin):
        app = App.get_running_app()
        lbl = Label(text="Import ho raha hai...", halign="center")
        pop = popup("Import", lbl, (.8, .25), auto_dismiss=False)
        pop.open()

        def prog(i, n):
            Clock.schedule_once(lambda dt: setattr(lbl, "text", f"Import {i}/{n}"))

        def done(n, err):
            pop.dismiss()
            app.busy = False
            app.last = time.time()
            _rm(tmp)
            if err:
                info("Import fail", err[:150], .4)
                return
            info("Import ho gaya", f"{n} files wapas aa gayi.\nAb aapka naya PIN chalega.", .4)
            app.show_gallery()

        def work():
            n, err = 0, None
            try:
                n = app.store.import_backup(tmp, master, pin, prog)
            except Exception as e:
                err = str(e)
            Clock.schedule_once(lambda dt: done(n, err))

        threading.Thread(target=work, daemon=True).start()

    def set_auto(self, sec):
        st = App.get_running_app().store
        st.cfg["autolock"] = sec
        st.save()
        self.on_pre_enter()


# ---------------- Gallery Screen ----------------
class GalleryScreen(Screen):
    PAGE = 90

    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        self.names, self.limit = [], self.PAGE
        root = BoxLayout(orientation="vertical")

        tbox = BoxLayout(orientation="vertical")
        self.title = Label(font_size=dp(24), bold=True, halign="left", valign="bottom",
                           shorten=True)
        self.title.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.count = Label(font_size=dp(13), color=hx(MUTED), halign="left", valign="top",
                           shorten=True)
        self.count.bind(size=lambda w, s: setattr(w, "text_size", s))
        tbox.add_widget(self.title)
        tbox.add_widget(self.count)
        self.del_btn = btn("Del", self.del_album, RED, size_hint_x=None, width=0,
                           opacity=0, disabled=True)
        root.add_widget(header(tbox, [
            self.del_btn,
            btn("Lock", lambda: app.lock(), RED, size_hint_x=None, width=dp(62)),
        ], 64))

        frow = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8), padding=(dp(10), dp(4)))
        self.fbtns = {}
        for label, kind in [("All", "all"), ("Photos", "photo"), ("Videos", "video")]:
            b = btn(label, lambda k=kind: self.set_kind(k), radius=18)
            self.fbtns[kind] = b
            frow.add_widget(b)
        root.add_widget(frow)

        sv = ScrollView()
        self.grid = GridLayout(cols=3, spacing=dp(6), padding=dp(4),
                               size_hint_y=None, row_force_default=True)
        self.grid.bind(minimum_height=self.grid.setter("height"))
        sv.add_widget(self.grid)
        root.add_widget(sv)
        root.add_widget(nav_bar("gallery"))
        self.add_widget(root)

    def set_kind(self, k):
        App.get_running_app().kind = k
        self.refresh()

    def del_album(self):
        app = App.get_running_app()
        album = app.current_album
        if not album:
            return

        def go():
            app.store.delete_album(album)
            app.current_album = None
            self.refresh()
        ask("Album hatayein", f"'{album}' album hatayein?\n(Files vault mein hi rahengi)", go)

    def on_pre_enter(self, *a):
        self.refresh()

    def more(self):
        self.limit += self.PAGE
        self.refresh(False)

    def refresh(self, reset=True):
        app = App.get_running_app()
        st, album, kind = app.store, app.current_album, app.kind
        if reset:
            self.limit = self.PAGE
        self.grid.clear_widgets()
        if st.key is None:
            return
        self.names = st.album_items(album, kind) if album else st.media_list(kind)
        self.title.text = album if album else "Vault"
        if album:
            self.del_btn.width, self.del_btn.opacity, self.del_btn.disabled = dp(62), 1, False
        else:
            self.del_btn.width, self.del_btn.opacity, self.del_btn.disabled = 0, 0, True
        for k, b in self.fbtns.items():
            b.set_bg(ACCENT if k == kind else CARD)
        n = len(self.names)
        v = sum(1 for x in self.names if is_video(x))
        self.count.text = f"{n - v} photos  |  {v} videos" if n else "Kuch nahi hai - Import dabaiye"
        app.gen += 1
        gen = app.gen
        size = (Window.width - dp(8) - dp(12)) / 3
        self.grid.row_default_height = size
        for i, name in enumerate(self.names[:self.limit]):
            if is_video(name):
                t = RBtn(text="VIDEO\nPLAY", bg=SOFT, radius=10, halign="center")
            else:
                t = Thumb()
                app.thumb_q.put((gen, t, name, st.key))
            t.bind(on_release=lambda w, i=i: app.open_viewer(self.names, i))
            self.grid.add_widget(t)
        if n > self.limit:
            self.grid.add_widget(btn(f"+{n - self.limit}\naur", self.more, SOFT, radius=10))


# ---------------- Viewer Screen ----------------
class ViewerScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        self.names, self.i, self.vid, self.tok = [], 0, None, 0
        root = BoxLayout(orientation="vertical")
        self.pos_lbl = Label(font_size=dp(16), bold=True)
        root.add_widget(header(self.pos_lbl, [
            btn("Back", lambda: app.go("gallery"), SOFT, size_hint_x=None, width=dp(70))]))
        self.stage = SwipeStage(self.step)
        root.add_widget(self.stage)
        bar = BoxLayout(size_hint_y=None, height=dp(60), spacing=dp(5), padding=dp(6))
        bar.add_widget(btn("<", lambda: self.step(-1), SOFT, size_hint_x=.6))
        self.play_btn = btn("Pause", self.toggle_play, SOFT, font_size=dp(12))
        bar.add_widget(self.play_btn)
        bar.add_widget(btn("+Album", self.pick_album, ACCENT, font_size=dp(12)))
        bar.add_widget(btn("Restore", self.restore, GREEN, font_size=dp(12)))
        bar.add_widget(btn("Delete", self.delete, RED, font_size=dp(12)))
        bar.add_widget(btn(">", lambda: self.step(1), SOFT, size_hint_x=.6))
        root.add_widget(bar)
        self.add_widget(root)

    def show(self, names, i):
        self.names, self.i = names, i
        self.load()

    def stop_video(self):
        if self.vid is not None:
            try:
                self.vid.state = "stop"
                self.vid.unload()
            except Exception:
                pass
            self.vid = None

    def on_leave(self, *a):
        self.tok += 1
        self.stop_video()
        self.stage.clear_widgets()

    def load(self):
        app = App.get_running_app()
        self.tok += 1
        tok = self.tok
        self.stop_video()
        self.stage.clear_widgets()
        if not self.names:
            app.go("gallery")
            return
        self.i %= len(self.names)
        name = self.names[self.i]
        self.pos_lbl.text = f"{self.i + 1} / {len(self.names)}"
        vid = is_video(name)
        self.play_btn.disabled = not vid
        self.play_btn.text = "Pause" if vid else "-"
        self.stage.add_widget(Label(text="Decrypting..."))
        key = app.store.key

        def work():
            try:
                p, err = app.store.decrypt_cached(name, key), None
            except Exception as e:
                p, err = None, str(e)
            Clock.schedule_once(lambda dt: self.ready(tok, name, p, err))
        threading.Thread(target=work, daemon=True).start()

    def ready(self, tok, name, path, err):
        if tok != self.tok:
            return
        self.stage.clear_widgets()
        if err:
            self.stage.add_widget(Label(text="Open nahi hua: " + err[:100]))
            return
        if is_video(name):
            if Video is None:
                self.stage.add_widget(Label(text="Video player available nahi"))
                return
            try:
                self.vid = Video(source=path, state="play", options={"eos": "loop"})
                self.stage.add_widget(self.vid)
            except Exception:
                self.stage.add_widget(Label(text="Ye video play nahi ho paayi"))
        else:
            self.stage.add_widget(Image(source=path))

    def toggle_play(self):
        if self.vid is None:
            return
        if self.vid.state == "play":
            self.vid.state = "pause"
            self.play_btn.text = "Play"
        else:
            self.vid.state = "play"
            self.play_btn.text = "Pause"

    def step(self, d):
        if not self.names:
            return
        self.i += d
        self.load()

    def delete(self):
        def go():
            app = App.get_running_app()
            self.stop_video()
            app.store.delete(self.names[self.i])
            self.names.pop(self.i)
            self.load()
        ask("Delete", "Ye file vault se hamesha ke liye delete karein?", go)

    def restore(self):
        app = App.get_running_app()
        name = self.names[self.i]

        def go():
            self.stop_video()
            key = app.store.key
            lbl = Label(text="Restore ho raha hai...", halign="center")
            pop = popup("Restore", lbl, (.8, .25), auto_dismiss=False)
            pop.open()
            app.busy = True

            def done(dest, err):
                pop.dismiss()
                app.busy = False
                app.last = time.time()
                if err:
                    info("Restore fail", err[:150], .35)
                    self.load()
                    return
                app.store.delete(name)
                if name in self.names:
                    self.names.remove(name)
                info("Restore ho gayi",
                     "File Gallery mein (VaultRestored folder) wapas aa gayi aur vault se hat gayi.\n"
                     "Gallery mein dikhne mein thoda time lag sakta hai.", .4)
                self.load()

            def work():
                try:
                    dest, err = app.store.restore_file(name, key), None
                except Exception as e:
                    dest, err = None, str(e)
                Clock.schedule_once(lambda dt: done(dest, err))
            threading.Thread(target=work, daemon=True).start()
        ask("Restore", "Ye file wapas phone storage mein chali jayegi\naur vault se hat jayegi. Karein?", go)

    def pick_album(self):
        app = App.get_running_app()
        name = self.names[self.i]
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        pop = popup("Add to Album", box, (.85, .6))
        for a in app.store.cfg["albums"]:
            box.add_widget(btn(a, lambda a=a: (app.store.add_to_album(a, name), pop.dismiss())))
        box.add_widget(btn("+ New Album", lambda: (
            pop.dismiss(),
            new_album_popup(lambda n: app.store.add_to_album(n, name))), ACCENT))
        pop.open()


# ---------------- Albums Screen ----------------
class AlbumsScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        root = BoxLayout(orientation="vertical")
        root.add_widget(header(title_label("Albums"), [
            btn("+ New", lambda: new_album_popup(lambda n: self.refresh()), ACCENT,
                size_hint_x=None, width=dp(76))], 64))
        sv = ScrollView()
        self.grid = GridLayout(cols=2, spacing=dp(10), padding=dp(10),
                               size_hint_y=None, row_force_default=True)
        self.grid.bind(minimum_height=self.grid.setter("height"))
        sv.add_widget(self.grid)
        root.add_widget(sv)
        root.add_widget(nav_bar("albums"))
        self.add_widget(root)

    def on_pre_enter(self, *a):
        self.refresh()

    def refresh(self):
        app = App.get_running_app()
        st = app.store
        self.grid.clear_widgets()
        if st.key is None:
            return
        app.gen += 1
        gen = app.gen
        w = (Window.width - dp(30)) / 2
        self.grid.row_default_height = w + dp(50)

        def open_album(a):
            app.current_album = a
            app.go("gallery")

        def add(title, names, a):
            card = AlbumCard(title, len(names))
            card.bind(on_release=lambda *x, a=a: open_album(a))
            photos = [n for n in names if not is_video(n)]
            if photos:
                app.thumb_q.put((gen, card.cover, photos[0], st.key))
            self.grid.add_widget(card)
        add("All Media", st.media_list(), None)
        for a in list(st.cfg["albums"]):
            add(a, st.album_items(a), a)


# ---------------- Import Screen (custom browser) ----------------
class ImportScreen(Screen):
    PAGE = 120

    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        self.move = False
        self.sel = set()
        self.path = ROOT
        self.limit = self.PAGE
        self.dirs, self.media, self.other, self.err = [], [], 0, ""

        root = BoxLayout(orientation="vertical")
        root.add_widget(header(title_label("Import Media"), [
            btn("Help", self.help, SOFT, size_hint_x=None, width=dp(64)),
            btn("Back", lambda: app.go("gallery"), SOFT, size_hint_x=None, width=dp(70))], 64))

        quick = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6), padding=(dp(8), 0))
        for label, sub in [("DCIM", "DCIM"), ("Pictures", "Pictures"),
                           ("Movies", "Movies"), ("Download", "Download")]:
            quick.add_widget(btn(label, lambda s=sub: self.load(os.path.join(ROOT, s)),
                                 radius=18, font_size=dp(13)))
        root.add_widget(quick)

        self.path_lbl = Label(size_hint_y=None, height=dp(26), font_size=dp(12),
                              color=hx(MUTED), halign="left", shorten=True,
                              shorten_from="left")
        self.path_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
        root.add_widget(self.path_lbl)

        self.status = Label(size_hint_y=None, height=dp(54), font_size=dp(13),
                            halign="center", color=hx("#FFD166"))
        self.status.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        root.add_widget(self.status)

        sv = ScrollView()
        self.box = BoxLayout(orientation="vertical", spacing=dp(4), padding=dp(6),
                             size_hint_y=None)
        self.box.bind(minimum_height=self.box.setter("height"))
        sv.add_widget(self.box)
        root.add_widget(sv)

        self.sel_lbl = Label(size_hint_y=None, height=dp(24), color=hx(MUTED))
        root.add_widget(self.sel_lbl)

        row1 = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6), padding=(dp(8), 0))
        row1.add_widget(btn("Select All", self.select_all))
        row1.add_widget(btn("Clear", self.clear_sel))
        self.move_btn = btn("Move: OFF", self.toggle_move)
        row1.add_widget(self.move_btn)
        root.add_widget(row1)

        row2 = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(6), padding=(dp(8), dp(6)))
        row2.add_widget(btn("Import Selected", self.import_selected, GREEN))
        row2.add_widget(btn("Import Folder (All)", self.import_folder, ACCENT))
        root.add_widget(row2)
        self.add_widget(root)

    def diagnose(self):
        lines = []
        for sub in ("DCIM/Camera", "DCIM", "Pictures", "Download", "Movies"):
            p = os.path.join(ROOT, sub)
            try:
                names = os.listdir(p)
                m = sum(1 for n in names if n.lower().endswith(ALL_EXTS))
                lines.append(f"{sub}: {len(names)} items, {m} media")
            except Exception as e:
                lines.append(f"{sub}: ERROR {str(e)[:40]}")
        return "\n".join(lines)

    def help(self):
        info("Files nahi dikh rahi?",
             "TEST:\n" + self.diagnose() + "\n\n"
             "Agar 'ERROR' ya '0 media' hai to permission baaki hai:\n"
             "Settings > Apps > Pydroid 3 > Permissions mein Files and media / "
             "Photos and videos ALLOW karein, aur Special app access > "
             "All files access > Pydroid 3 ON karein. Phir Force stop karke dobara kholein.", .75)

    def start_gallery_pick(self):
        app = App.get_running_app()
        if app.store.key is None:
            return False
        app.busy = True  # picker khulne par app auto-lock na ho

        def got(uris):
            Clock.schedule_once(lambda dt: self.picked(uris))

        if open_gallery_picker(got):
            return True
        app.busy = False
        return False

    def picked(self, uris):
        app = App.get_running_app()
        app.last = time.time()
        key = app.store.key
        if not uris or key is None:
            app.busy = False
            app.go("gallery")
            return
        total = len(uris)
        tmpdir = os.path.join(app.user_data_dir, "tmp_import")
        lbl = Label(text=f"Importing 0/{total}", halign="center")
        pop = popup("Encrypt + Import ho raha hai...", lbl, (.8, .25), auto_dismiss=False)
        pop.open()

        def finish(ok, err):
            pop.dismiss()
            app.busy = False
            app.last = time.time()
            msg = f"{ok}/{total} files vault mein add ho gayi!"
            if ok:
                msg += "\n\nAsli files Gallery mein abhi bhi hain. Vault mein dekh kar tasalli ho jaye to unhe Gallery se khud delete kar dein."
            if ok < total and err:
                msg += "\nError: " + err[:120]
            info("Done", msg, .5)
            app.show_gallery()

        def work():
            ok, last_err = 0, ""
            for i, u in enumerate(uris):
                tmp = None
                try:
                    tmp = copy_uri_to_file(u, tmpdir, i)
                    e = app.store.add_one(tmp, i, True, key)
                except Exception as ex:
                    e = str(ex)
                if e is None:
                    ok += 1
                else:
                    last_err = e
                    if tmp:
                        _rm(tmp)
                Clock.schedule_once(
                    lambda dt, i=i: setattr(lbl, "text", f"Importing {i + 1}/{total}"))
            Clock.schedule_once(lambda dt: finish(ok, last_err))

        threading.Thread(target=work, daemon=True).start()

    def on_pre_enter(self, *a):
        if self.start_gallery_pick():
            return
        start = self.path if os.path.exists(self.path) else ROOT
        if not os.path.exists(start):
            start = os.path.expanduser("~")
        self.load(start)

    # ----- folder scan -----
    def load(self, path):
        self.path, self.limit = path, self.PAGE
        dirs, media, other, err = [], [], 0, ""
        try:
            names = os.listdir(path)
        except Exception as e:
            names, err = [], str(e)
        for n in names:
            if n.startswith("."):
                continue
            p = os.path.join(path, n)
            try:
                if os.path.isdir(p):
                    dirs.append(n)
                elif n.lower().endswith(ALL_EXTS):
                    media.append(p)
                else:
                    other += 1
            except Exception:
                pass

        def mt(p):
            try:
                return os.path.getmtime(p)
            except Exception:
                return 0
        dirs.sort(key=str.lower)
        media.sort(key=mt, reverse=True)
        self.dirs, self.media, self.other, self.err = dirs, media, other, err
        self.draw()

    def draw(self):
        self.box.clear_widgets()
        self.path_lbl.text = self.path
        if self.err:
            self.status.text = ("Folder khul nahi raha: " + self.err +
                                "\nPhone Settings > Apps > Pydroid 3 > Permissions > "
                                "Files/Photos and videos > ALLOW (All files access)")
        elif not (self.dirs or self.media or self.other):
            self.status.text = ("Folder khali hai ya Android ne access block kiya.\n"
                                "Pydroid 3 ko Settings mein 'All files access' dein.")
        else:
            t = (f"{len(self.dirs)} folders  |  {len(self.media)} photos/videos"
                 f"  |  {self.other} other files")
            if self.path != ROOT and not self.media and not self.other:
                t += "\nFiles nahi dikh rahi? Upar Help dabaiye"
            self.status.text = t
        self.sel_lbl.text = f"{len(self.sel)} selected"

        up = os.path.dirname(self.path.rstrip("/"))
        if up and up != self.path:
            self.box.add_widget(btn("[ .. ]  Upar jaayein", lambda: self.load(up),
                                    SOFT, size_hint_y=None, height=dp(44), radius=10))
        for d in self.dirs:
            self.box.add_widget(btn("[DIR]  " + d,
                                    lambda d=d: self.load(os.path.join(self.path, d)),
                                    CARD, size_hint_y=None, height=dp(44), radius=10))
        for p in self.media[:self.limit]:
            self.box.add_widget(self.media_btn(p))
        if len(self.media) > self.limit:
            self.box.add_widget(btn(f"Aur dikhaiye ({len(self.media) - self.limit} baaki)",
                                    self.more, SOFT, size_hint_y=None, height=dp(44), radius=10))

    def media_btn(self, p):
        name = os.path.basename(p)
        icon = "VID" if is_video(name) else "IMG"
        on = p in self.sel
        b = RBtn(text=("[X] " if on else "[  ] ") + icon + "  " + name,
                 bg=GREEN if on else CARD, radius=10,
                 size_hint_y=None, height=dp(44), halign="left", shorten=True,
                 shorten_from="right")
        b.bind(size=lambda w, s: setattr(w, "text_size", (s[0] - dp(16), None)))

        def toggle(*a):
            if p in self.sel:
                self.sel.discard(p)
                b.text = "[  ] " + icon + "  " + name
                b.set_bg(CARD)
            else:
                self.sel.add(p)
                b.text = "[X] " + icon + "  " + name
                b.set_bg(GREEN)
            self.sel_lbl.text = f"{len(self.sel)} selected"
        b.bind(on_release=toggle)
        return b

    def more(self):
        self.limit += self.PAGE
        self.draw()

    def select_all(self):
        self.sel.update(self.media)
        self.draw()

    def clear_sel(self):
        self.sel.clear()
        self.draw()

    def toggle_move(self):
        self.move = not self.move
        if self.move:
            self.move_btn.text = "Move: ON"
            self.move_btn.set_bg(RED)
        else:
            self.move_btn.text = "Move: OFF"
            self.move_btn.set_bg(CARD)

    def import_selected(self):
        files = [f for f in sorted(self.sel) if os.path.isfile(f)]
        if not files:
            info("Select karein", "Pehle photos/videos tap karke select karein.")
            return
        self.run_import(files)

    def import_folder(self):
        files = list(self.media)
        if not files:
            info("Khali folder", "Is folder mein photo/video nahi hai.")
            return
        ask("Import Folder", f"Is folder ki {len(files)} files\nimport karein?",
            lambda: self.run_import(files))

    def run_import(self, files):
        app = App.get_running_app()
        move, total, key = self.move, len(files), app.store.key
        lbl = Label(text=f"Importing 0/{total}", halign="center")
        pop = popup("Encrypt + Import ho raha hai...", lbl, (.8, .25), auto_dismiss=False)
        pop.open()
        app.busy = True

        def finish(ok, err):
            pop.dismiss()
            app.busy = False
            app.last = time.time()
            self.sel.clear()
            msg = f"{ok}/{total} files vault mein add ho gayi!"
            if ok < total and err:
                msg += "\nError: " + err[:120]
            info("Done", msg, .35)
            app.show_gallery()

        def work():
            ok, last_err = 0, ""
            for i, p in enumerate(files):
                e = app.store.add_one(p, i, move, key)
                if e is None:
                    ok += 1
                else:
                    last_err = e
                Clock.schedule_once(
                    lambda dt, i=i: setattr(lbl, "text", f"Importing {i + 1}/{total}"))
            Clock.schedule_once(lambda dt: finish(ok, last_err))

        threading.Thread(target=work, daemon=True).start()


# ---------------- App ----------------
class VaultApp(App):
    current_album = None
    kind = "all"
    gen = 0
    busy = False
    last = 0

    def build(self):
        self.title = "Private Gallery Vault"
        base = os.path.join(self.user_data_dir, "vault")
        os.makedirs(base, exist_ok=True)
        self.store = Store(base, os.path.join(self.user_data_dir, "vault_cache"))
        self.thumb_q = queue.Queue()
        for _ in range(2):
            threading.Thread(target=self.thumb_worker, daemon=True).start()
        self.last = time.time()
        self.sm = ScreenManager(transition=SlideTransition(duration=.2))
        for cls, name in [(PinScreen, "pin"), (GalleryScreen, "gallery"),
                          (ViewerScreen, "viewer"), (AlbumsScreen, "albums"),
                          (ImportScreen, "import"), (SettingsScreen, "settings"),
                          (ChangePinScreen, "changepin")]:
            self.sm.add_widget(cls(name=name))
        self.sm.current = "pin"
        Window.bind(on_keyboard=self.on_key, on_touch_down=self.touched)
        Clock.schedule_interval(self.tick, 1)
        return self.sm

    def on_start(self):
        ask_android_permissions()

    # ----- navigation -----
    def go(self, name):
        self.sm.current = name

    def show_gallery(self):
        """Gallery tab: sab media dikhata hai (album filter hata deta hai)."""
        self.current_album = None
        if self.sm.current == "gallery":
            self.sm.get_screen("gallery").refresh()
        else:
            self.go("gallery")

    def open_viewer(self, names, i):
        self.sm.get_screen("viewer").show(list(names), i)
        self.sm.current = "viewer"

    # ----- thumbnails -----
    def thumb_worker(self):
        while True:
            gen, w, name, key = self.thumb_q.get()
            if gen != self.gen or self.store.key is None:
                continue
            try:
                p = self.store.thumb_cached(name, key)
            except Exception:
                continue
            Clock.schedule_once(lambda dt, w=w, p=p, gen=gen: self.set_thumb(gen, w, p))

    def set_thumb(self, gen, w, p):
        if gen == self.gen and self.store.key is not None:
            w.source = p

    # ----- lock / unlock -----
    def after_unlock(self):
        self.last = time.time()
        if self.store.plain_files():
            self.migrate()
        else:
            self.go("gallery")

    def migrate(self):
        st = self.store
        self.busy = True
        lbl = Label(text="Purani files encrypt ho rahi hain...", halign="center")
        pop = popup("Encrypting...", lbl, (.8, .25), auto_dismiss=False)
        pop.open()

        def done(dt):
            pop.dismiss()
            self.busy = False
            self.last = time.time()
            self.go("gallery")

        def cb(i, n):
            Clock.schedule_once(lambda dt: setattr(lbl, "text", f"Encrypting {i}/{n}"))

        def work():
            try:
                st.migrate_plain(cb)
            except Exception:
                pass
            Clock.schedule_once(done)
        threading.Thread(target=work, daemon=True).start()

    def lock(self):
        st = self.store
        if st.key is None:
            return
        st.key = None
        self.current_album = None
        self.gen += 1
        v = self.sm.get_screen("viewer")
        v.tok += 1
        v.stop_video()
        v.stage.clear_widgets()
        self.sm.get_screen("gallery").grid.clear_widgets()
        self.sm.get_screen("albums").grid.clear_widgets()
        for c in ("kv.image", "kv.texture"):
            try:
                Cache.remove(c)
            except Exception:
                pass
        st.clear_cache()
        self.go("pin")

    def touched(self, *a):
        self.last = time.time()
        return False

    def tick(self, dt):
        st = self.store
        al = st.cfg.get("autolock", 60)
        if st.key is None or al <= 0 or self.busy:
            return
        if self.sm.current == "viewer":
            v = self.sm.get_screen("viewer")
            if v.vid is not None and v.vid.state == "play":
                self.last = time.time()
                return
        if time.time() - self.last > al:
            self.lock()

    def on_pause(self):
        if not self.busy:
            self.lock()
        return True

    def on_resume(self):
        self.last = time.time()

    def on_stop(self):
        self.store.clear_cache()

    def on_key(self, win, key, *a):
        if key != 27:
            return False
        cur = self.sm.current
        if cur == "pin":
            return False
        if cur == "gallery":
            if self.current_album:
                self.current_album = None
                self.sm.get_screen("gallery").refresh()
            else:
                self.lock()
        elif cur == "changepin":
            self.go("settings")
        else:
            self.go("gallery")
        return True


if __name__ == "__main__":
    VaultApp().run()
