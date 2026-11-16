"""Sobe o serviço de verdade (bentoml serve) uma vez por sessão de testes.

- `url`: modo real, com Ollama (tests/test_api.py);
- `servico_simulado`: BACKEND=simulado, sem Ollama, com o log numa pasta temporária e arquivos
  pequenos para forçar a rotação (tests/test_monitoramento.py).
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

RAIZ = Path(__file__).parent.parent


def porta_livre() -> int:
    # Porta escolhida pelo sistema: não briga com um "just serve" ou outro serviço aberto.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def subir(env_extra: dict, home: Path) -> tuple[subprocess.Popen, str]:
    porta = porta_livre()
    # BENTOML_HOME próprio: cada "bentoml serve" apaga $BENTOML_HOME/prometheus_multiproc_dir ao subir;
    # sem isso, rodar os testes zeraria o /metrics de um "just serve" aberto na mesma máquina.
    env_extra = {**env_extra, "BENTOML_HOME": str(home)}
    servidor = subprocess.Popen(
        [sys.executable, "-m", "bentoml", "serve", "service:TriagemMedidor", "--port", str(porta)],
        cwd=RAIZ,
        env={**os.environ, **env_extra},
    )
    base = f"http://localhost:{porta}"
    # Espera até 60 s o servidor responder pronto no /readyz (rota padrão do BentoML).
    for _ in range(60):
        if servidor.poll() is not None:
            pytest.fail("O serviço não subiu. Veja o erro acima (Ollama aberto? modelo baixado?).")
        try:
            if httpx.get(f"{base}/readyz").status_code == 200:
                return servidor, base
        except httpx.ConnectError:
            pass
        time.sleep(1)
    servidor.terminate()
    pytest.fail("O serviço não ficou pronto em 60 s.")


@pytest.fixture(scope="session")
def url(tmp_path_factory):
    servidor, base = subir({"BACKEND": "ollama"}, tmp_path_factory.mktemp("bentoml_real"))
    yield base
    servidor.terminate()
    servidor.wait()


@pytest.fixture(scope="session")
def servico_simulado(tmp_path_factory):
    pasta_log = tmp_path_factory.mktemp("logs")
    env = {
        "BACKEND": "simulado",
        "SIMULADO_LATENCIA_S": "0.01",
        "LOG_DIR": str(pasta_log),
        "LOG_MAX_BYTES": "6000",  # ~3 pedidos por arquivo: a rotação acontece durante os testes
        "LOG_BACKUPS": "50",
    }
    servidor, base = subir(env, tmp_path_factory.mktemp("bentoml_simulado"))
    yield base, pasta_log
    servidor.terminate()
    servidor.wait()
