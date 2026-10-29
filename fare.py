"""Fareyi azar azar kımıldatır. İstediğinde kısayolla açılır ve kapanır.

Windows'ta çalışır. Ek paket gerekmez.

Klavye kancası kurmaz. ``RegisterHotKey`` yalnızca aç/kapa ve çıkış tuşlarını
dinler; Win, Alt+Tab ve diğer kısayollar normal çalışmaya devam eder.
"""

from __future__ import annotations

import argparse
import ctypes
import signal
import sys
import threading
import time

# Windows LLP64 düzeni. Linux'ta da aynı bayt genişlikleriyle sınanır.
LONG = ctypes.c_int32
DWORD = ctypes.c_uint32
UINT = ctypes.c_uint32
BOOL = ctypes.c_int
ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_REMOVE = 0x0001

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# Sistem kısayolu değil. Başka bir program bu ikisini tutuyorsa harfi değiştir.
MOD = MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT
TUS_AC = 0x4D  # M
TUS_CIK = 0x51  # Q
KIMLIK_AC = 1
KIMLIK_CIK = 2

KISAYOL_AC = "Ctrl+Alt+Shift+M"
KISAYOL_CIK = "Ctrl+Alt+Shift+Q"


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class INPUT(ctypes.Structure):
    _fields_ = (
        ("type", DWORD),
        ("mi", MOUSEINPUT),
    )


def beklenen_girdi_boyutu() -> int:
    return 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28


def kare_adimi(adim: int, mesafe: int) -> tuple[int, int]:
    """Karenin bir kenarı boyunca tek piksellik adım. Dört kenar başa döner."""
    if mesafe < 1:
        raise ValueError("mesafe en az 1 olmalı")
    kenar = (adim // mesafe) % 4
    return ((1, 0), (0, 1), (-1, 0), (0, -1))[kenar]


def tur_noktalari(mesafe: int, baslangic: tuple[int, int] = (0, 0)) -> list[tuple[int, int]]:
    x, y = baslangic
    noktalar = [(x, y)]
    for adim in range(mesafe * 4):
        dx, dy = kare_adimi(adim, mesafe)
        x += dx
        y += dy
        noktalar.append((x, y))
    return noktalar


def ekrana_sigdir(
    x: int, y: int, sol: int, ust: int, genislik: int, yukseklik: int
) -> tuple[int, int]:
    return (
        min(max(x, sol), sol + genislik - 1),
        min(max(y, ust), ust + yukseklik - 1),
    )


def mutlak_olcek(
    x: int, y: int, sol: int, ust: int, genislik: int, yukseklik: int
) -> tuple[int, int]:
    """Pikseli SendInput'un 0..65535 sanal masaüstü ölçeğine çevirir."""
    x, y = ekrana_sigdir(x, y, sol, ust, genislik, yukseklik)
    ax = int(round((x - sol) * 65535 / max(genislik - 1, 1)))
    ay = int(round((y - ust) * 65535 / max(yukseklik - 1, 1)))
    return ax, ay


class FareDurumu:
    """Açıkken birer piksel ilerler. Sen fareyi oynatınca bir süre karışmaz."""

    def __init__(self) -> None:
        self.acik = False
        self.adim = 0
        self.biraktigimiz: tuple[int, int] | None = None
        self.gordugumuz: tuple[int, int] | None = None
        self.sakin_an: float | None = None

    def ac_kapa(self) -> bool:
        self.acik = not self.acik
        self.adim = 0
        self.biraktigimiz = None
        self.gordugumuz = None
        self.sakin_an = None
        return self.acik

    def sonraki(
        self,
        konum: tuple[int, int],
        simdi: float,
        mesafe: int,
        bekle: float,
    ) -> tuple[int, int] | None:
        if not self.acik:
            return None

        if self.biraktigimiz is None:
            self.biraktigimiz = konum
            self.gordugumuz = konum
            self.sakin_an = simdi

        if konum != self.gordugumuz:
            self.gordugumuz = konum
            self.sakin_an = simdi

        if konum != self.biraktigimiz:
            if self.sakin_an is None or simdi - self.sakin_an < bekle:
                return None
            self.biraktigimiz = konum

        dx, dy = kare_adimi(self.adim, mesafe)
        self.adim += 1
        return dx, dy

    def kaydet(self, konum: tuple[int, int], simdi: float) -> None:
        self.biraktigimiz = konum
        self.gordugumuz = konum
        self.sakin_an = simdi


def denetle(aralik: float, mesafe: int, bekle: float) -> None:
    if aralik <= 0:
        raise SystemExit("Aralık sıfırdan büyük olmalı. Örnek: --aralik 1")
    if mesafe < 1:
        raise SystemExit("Mesafe en az 1 piksel olmalı. Örnek: --mesafe 16")
    if bekle < 0:
        raise SystemExit("Bekleme negatif olamaz.")


def argumanlar(argv: list[str] | None = None) -> argparse.Namespace:
    cozum = argparse.ArgumentParser(
        prog="fare.py",
        description="Fareyi küçük bir karenin çevresinde, birer piksel kımıldatır.",
    )
    cozum.add_argument(
        "--aralik",
        type=float,
        default=2.0,
        help="iki piksel arasındaki saniye (varsayılan 2)",
    )
    cozum.add_argument(
        "--mesafe",
        type=int,
        default=4,
        help="karenin kenarı, piksel (varsayılan 4). 1 yerinde bir piksel oynatır",
    )
    cozum.add_argument(
        "--bekle",
        type=float,
        default=2.0,
        help="sen fareyi oynatınca yeniden başlamadan önce beklenecek saniye",
    )
    cozum.add_argument(
        "--baslangicta-acik",
        action="store_true",
        help="kısayolu beklemeden hareketi başlat",
    )
    cozum.add_argument(
        "--dene",
        action="store_true",
        help="fareye dokunmadan bir turu yazdırıp çık",
    )
    return cozum.parse_args(argv)


def dene(mesafe: int) -> None:
    noktalar = tur_noktalari(mesafe)
    yaz(f"Deneme: kenar {mesafe} piksel, {len(noktalar) - 1} adım, imleç başa döner.")
    yaz(" -> ".join(f"({x}, {y})" for x, y in noktalar))
    if noktalar[0] != noktalar[-1]:
        raise SystemExit("Tur başa dönmedi.")
    yaz("Bitiş başlangıçla aynı.")


_yaz_kilit = threading.Lock()


def yaz(metin: str) -> None:
    with _yaz_kilit:
        print(metin, flush=True)


def durum_yaz(acik: bool) -> None:
    if acik:
        yaz(f"Açık. Fare azar azar kımıldıyor. Kapatmak için {KISAYOL_AC}.")
    else:
        yaz(f"Kapalı. Fare durdu. Açmak için {KISAYOL_AC}.")


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def _imleci_oku(user32) -> tuple[int, int]:
    class POINT(ctypes.Structure):
        _fields_ = (("x", LONG), ("y", LONG))

    nokta = POINT()
    if not user32.GetCursorPos(ctypes.byref(nokta)):
        raise OSError("İmleç okunamadı")
    return int(nokta.x), int(nokta.y)


def _imleci_gotur(user32, x: int, y: int) -> tuple[int, int]:
    if ctypes.sizeof(INPUT) != beklenen_girdi_boyutu():
        raise OSError("Fare girdisi yapısı bu Windows sürümüyle uyuşmuyor")
    sol = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    ust = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    genislik = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    yukseklik = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    if genislik < 1 or yukseklik < 1:
        raise OSError("Ekran boyutu okunamadı")
    ax, ay = mutlak_olcek(x, y, sol, ust, genislik, yukseklik)
    girdi = INPUT()
    girdi.type = INPUT_MOUSE
    girdi.mi.dx = ax
    girdi.mi.dy = ay
    girdi.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    gonderilen = user32.SendInput(1, ctypes.byref(girdi), ctypes.sizeof(INPUT))
    if gonderilen != 1:
        raise OSError("Fare kımıldatılamadı")
    return _imleci_oku(user32)


def _tus_kaydet(user32, kimlik: int, tus: int) -> bool:
    if user32.RegisterHotKey(None, kimlik, MOD, tus):
        return True
    return bool(user32.RegisterHotKey(None, kimlik, MOD & ~MOD_NOREPEAT, tus))


def _bip(acik: bool) -> None:
    try:
        import winsound

        winsound.Beep(880 if acik else 440, 70)
    except (RuntimeError, OSError):
        pass


def _calistir(dur: threading.Event, durum: FareDurumu, kilit: threading.Lock, args) -> None:
    user32 = _user32()
    while not dur.is_set():
        with kilit:
            acik = durum.acik
        if not acik:
            dur.wait(0.2)
            continue
        try:
            konum = _imleci_oku(user32)
            with kilit:
                delta = durum.sonraki(konum, time.monotonic(), args.mesafe, args.bekle)
            if delta is None:
                dur.wait(0.2)
                continue
            gidilen = _imleci_gotur(user32, konum[0] + delta[0], konum[1] + delta[1])
            with kilit:
                durum.kaydet(gidilen, time.monotonic())
        except OSError as hata:
            yaz(f"Hata: {hata}")
            dur.wait(1.0)
            continue
        dur.wait(args.aralik)


def _mesaj_dongusu(user32, dur: threading.Event, kisayol) -> None:
    class MSG(ctypes.Structure):
        _fields_ = (
            ("hwnd", ctypes.c_void_p),
            ("message", UINT),
            ("wParam", ULONG_PTR),
            ("lParam", ULONG_PTR),
            ("time", DWORD),
            ("pt_x", LONG),
            ("pt_y", LONG),
        )

    ileti = MSG()
    while not dur.is_set():
        while user32.PeekMessageW(ctypes.byref(ileti), None, 0, 0, PM_REMOVE):
            if ileti.message == WM_HOTKEY:
                kisayol(int(ileti.wParam))
            elif ileti.message == WM_QUIT:
                dur.set()
                return
            user32.TranslateMessage(ctypes.byref(ileti))
            user32.DispatchMessageW(ctypes.byref(ileti))
        time.sleep(0.05)


def pencerede_calistir(args: argparse.Namespace) -> None:
    if sys.platform != "win32":
        raise SystemExit("Bu betik Windows'ta çalışır. Yolu görmek için: python fare.py --dene")

    user32 = _user32()
    user32.RegisterHotKey.argtypes = (ctypes.c_void_p, ctypes.c_int, UINT, UINT)
    user32.RegisterHotKey.restype = BOOL
    user32.UnregisterHotKey.argtypes = (ctypes.c_void_p, ctypes.c_int)
    user32.PeekMessageW.argtypes = (ctypes.c_void_p, ctypes.c_void_p, UINT, UINT, UINT)
    user32.PeekMessageW.restype = BOOL
    user32.SendInput.argtypes = (UINT, ctypes.c_void_p, ctypes.c_int)
    user32.SendInput.restype = UINT
    user32.GetCursorPos.argtypes = (ctypes.c_void_p,)
    user32.GetCursorPos.restype = BOOL
    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    user32.GetSystemMetrics.restype = ctypes.c_int

    if not _tus_kaydet(user32, KIMLIK_AC, TUS_AC):
        raise SystemExit(
            f"{KISAYOL_AC} başka bir programda kayıtlı. fare.py içinde TUS_AC harfini değiştir."
        )
    if not _tus_kaydet(user32, KIMLIK_CIK, TUS_CIK):
        user32.UnregisterHotKey(None, KIMLIK_AC)
        raise SystemExit(
            f"{KISAYOL_CIK} başka bir programda kayıtlı. fare.py içinde TUS_CIK harfini değiştir."
        )

    try:
        baslik = ctypes.WinDLL("kernel32", use_last_error=True)
        baslik.SetConsoleTitleW("Fare kımıldatıcı")
    except OSError:
        pass

    durum = FareDurumu()
    kilit = threading.Lock()
    dur = threading.Event()
    if args.baslangicta_acik:
        with kilit:
            durum.ac_kapa()

    def kisayol(kimlik: int) -> None:
        if kimlik == KIMLIK_CIK:
            yaz("Çıkılıyor.")
            dur.set()
            user32.PostQuitMessage(0)
            return
        if kimlik != KIMLIK_AC:
            return
        with kilit:
            acik = durum.ac_kapa()
        _bip(acik)
        durum_yaz(acik)

    def durdur(_signum, _frame) -> None:
        dur.set()
        user32.PostQuitMessage(0)

    signal.signal(signal.SIGINT, durdur)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, durdur)

    isci = threading.Thread(
        target=_calistir,
        args=(dur, durum, kilit, args),
        name="fare",
        daemon=True,
    )
    isci.start()

    yaz("Fare kımıldatıcı hazır.")
    yaz(f"Aç / kapat: {KISAYOL_AC}")
    yaz(f"Çıkış: bu pencerede Ctrl+C ya da {KISAYOL_CIK}")
    yaz("Windows kısayolları durmaz. Klavye kancası yok, yalnız bu iki tuş dinlenir.")
    yaz(f"Adım: 1 piksel, aralık: {args.aralik:g} sn, kare kenarı: {args.mesafe} piksel.")
    durum_yaz(durum.acik)
    try:
        _mesaj_dongusu(user32, dur, kisayol)
    finally:
        dur.set()
        user32.UnregisterHotKey(None, KIMLIK_AC)
        user32.UnregisterHotKey(None, KIMLIK_CIK)
        isci.join(timeout=1.5)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    args = argumanlar(argv)
    denetle(args.aralik, args.mesafe, args.bekle)
    if args.dene:
        dene(args.mesafe)
        return
    pencerede_calistir(args)


if __name__ == "__main__":
    main()
