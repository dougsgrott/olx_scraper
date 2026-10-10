import io
import os
import signal
import sys
import threading

from fakes import FakeSite, fake_sync_playwright
from olx_patchright import browser, warm


def run_warm(monkeypatch, capsys):
    fake = fake_sync_playwright(FakeSite(total=1))
    monkeypatch.setattr(warm, 'sync_playwright', fake)
    monkeypatch.setattr(sys, 'stdin', io.StringIO())  # no terminal
    warm.warm_profile('https://www.olx.com.br/imoveis/aluguel/estado-es')
    [ctx] = fake.playwright.chromium.contexts
    return ctx, capsys.readouterr().out


def test_without_a_terminal_a_signal_means_done(monkeypatch, capsys):
    timer = threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGUSR1))
    timer.start()
    handler_before = signal.getsignal(signal.SIGINT)
    ctx, out = run_warm(monkeypatch, capsys)
    assert ctx.closed
    assert 'signal done' in out and 'Profile saved' in out
    assert 'timed out' not in out
    assert signal.getsignal(signal.SIGINT) is handler_before
    assert browser.lock_holder()['pid'] == os.getpid()


def test_a_forgotten_warm_up_times_out_and_saves(monkeypatch, capsys):
    monkeypatch.setattr(warm, 'WARM_TIMEOUT_S', 0.3)
    ctx, out = run_warm(monkeypatch, capsys)
    assert ctx.closed
    assert 'timed out' in out and 'Profile saved' in out
