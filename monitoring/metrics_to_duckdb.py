"""Copia o /metrics para um banco relacional (DuckDB) a cada N segundos.

Cada coleta vira linhas na tabela `metricas` (uma por série: nome, rótulos, valor, hora da coleta).
Como o /metrics só tem o acumulado desde que o serviço subiu, guardar coletas periódicas é o que
permite calcular taxas e histogramas POR JANELA de tempo depois, em SQL (exemplos no monitoring/README.md).
Com --log, também carrega o logs/inferencia.jsonl na tabela `eventos` (uma linha por evento).

Uso:
  uv run python monitoring/metrics_to_duckdb.py --coletas 12 --intervalo 5     (1 min)
  uv run python monitoring/metrics_to_duckdb.py --coletas 0                    (até Ctrl+C)
  uv run python monitoring/metrics_to_duckdb.py --coletas 1 --log              (+ tabela eventos)
  uv run python monitoring/metrics_to_duckdb.py --consultas                    (roda monitoring/consultas.sql)
  (ou: just duckdb, just duckdb-consultas)
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb
import httpx
from prometheus_client.parser import text_string_to_metric_families

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
BANCO = Path(__file__).resolve().parent / "metrics.duckdb"
CONSULTAS = Path(__file__).resolve().parent / "consultas.sql"

TABELA = """
CREATE TABLE IF NOT EXISTS metricas (
    coletado_em TIMESTAMPTZ,  -- hora da coleta (relógio desta máquina)
    familia     VARCHAR,      -- ex.: bentoml_service_request_duration_seconds
    serie       VARCHAR,      -- ex.: ..._bucket, ..._count, ..._sum, ..._total
    tipo        VARCHAR,      -- counter, histogram, gauge
    endpoint    VARCHAR,      -- rótulo endpoint, quando existe (fica em coluna para facilitar o WHERE)
    http_code   VARCHAR,      -- rótulo http_response_code, quando existe
    le          DOUBLE,       -- limite superior do bucket (só nas séries _bucket; +Inf = infinito)
    rotulos     JSON,         -- todos os rótulos
    valor       DOUBLE
)
"""


def coletar(conexao, url: str) -> int:
    texto = httpx.get(f"{url}/metrics", timeout=10).text
    agora = datetime.now().astimezone()
    linhas = []
    for familia in text_string_to_metric_families(texto):
        for amostra in familia.samples:
            rotulos = dict(amostra.labels)
            le = rotulos.get("le")
            linhas.append(
                (agora, familia.name, amostra.name, familia.type, rotulos.get("endpoint"), rotulos.get("http_response_code"),
                 float(le) if le is not None else None, json.dumps(rotulos), float(amostra.value))
            )
    conexao.executemany("INSERT INTO metricas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", linhas)
    return len(linhas)


def carregar_log(conexao) -> int:
    import monitor_api  # lê inclusive os arquivos rotacionados

    eventos = [monitor_api.achatar(e) for e in monitor_api.ler_eventos()]
    if not eventos:
        return 0
    temporario = BANCO.with_suffix(".eventos.jsonl")
    temporario.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in eventos) + "\n", encoding="utf-8")
    conexao.execute("CREATE OR REPLACE TABLE eventos AS SELECT * FROM read_json_auto(?, format='newline_delimited')", [str(temporario)])
    temporario.unlink()
    return len(eventos)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--intervalo", type=float, default=5, help="segundos entre coletas")
    parser.add_argument("--coletas", type=int, default=12, help="quantas coletas (0 = até Ctrl+C)")
    parser.add_argument("--log", action="store_true", help="carrega também o log na tabela eventos")
    parser.add_argument("--consultas", action="store_true", help="só roda as consultas de monitoring/consultas.sql")
    args = parser.parse_args()
    if args.consultas:
        with duckdb.connect(str(BANCO), read_only=True) as conexao:
            for consulta in (c.strip() for c in CONSULTAS.read_text(encoding="utf-8").split(";")):
                if "SELECT" in consulta:
                    print(next(linha for linha in consulta.splitlines() if linha.startswith("-- ") and linha[3].isdigit()))
                    print(conexao.sql(consulta))
        return
    with duckdb.connect(str(BANCO)) as conexao:
        conexao.execute(TABELA)
        feitas = 0
        try:
            while args.coletas == 0 or feitas < args.coletas:
                if feitas:
                    time.sleep(args.intervalo)
                n = coletar(conexao, args.url)
                feitas += 1
                print(f"coleta {feitas}: {n} séries gravadas", flush=True)
        except KeyboardInterrupt:
            pass
        if args.log:
            print(f"eventos do log carregados: {carregar_log(conexao)}")
        total = conexao.execute("SELECT count(*), count(DISTINCT coletado_em) FROM metricas").fetchone()
        print(f"{BANCO.relative_to(RAIZ)}: {total[0]} linhas em metricas, de {total[1]} coletas")


if __name__ == "__main__":
    main()
