"""Sobe o serviço de verdade (bentoml serve) uma vez para todos os testes de API."""

import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

RAIZ = Path(__file__).parent.parent
PORTA = 3141  # porta diferente da 3000 para não brigar com um "just serve" aberto


@pytest.fixture(scope="session")
def url():
    servidor = subprocess.Popen(
        [sys.executable, "-m", "bentoml", "serve", "service:TriagemMedidor", "--port", str(PORTA)],
        cwd=RAIZ,
    )
    base = f"http://localhost:{PORTA}"
    # Espera até 60 s o servidor responder pronto no /readyz (rota padrão do BentoML).
    for _ in range(60):
        if servidor.poll() is not None:
            pytest.fail("O serviço não subiu. Veja o erro acima (Ollama aberto? modelo baixado?).")
        try:
            if httpx.get(f"{base}/readyz").status_code == 200:
                break
        except httpx.ConnectError:
            pass
        time.sleep(1)
    else:
        servidor.terminate()
        pytest.fail("O serviço não ficou pronto em 60 s.")
    yield base
    servidor.terminate()
    servidor.wait()
