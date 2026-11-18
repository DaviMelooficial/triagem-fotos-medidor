"""GET /monitor/*: lê o log (inclusive os arquivos rotacionados) e devolve as variáveis prontas para análise.

O BentoML só cria rotas POST com @bentoml.api; para GET montamos este app Starlette (que já vem com
o BentoML) em /monitor com @bentoml.asgi_app, no service.py.

- /monitor/variaveis  uma linha por evento, achatada (entrada.media_l, saida.confianca_geral...), JSON ou CSV
- /monitor/resumo     por variável: n, mínimo, Q1, mediana, Q3, máximo, média (boxplot) e contagens por faixa (histograma)
- /monitor/drift      KS e PSI de cada variável contra monitoring/referencia.json
- /monitor/referencia os valores do conjunto de referência, para quem quiser fazer a própria conta
Filtros comuns: inicio, fim (ISO 8601; sem fuso = hora local), evento (resposta|pedido|todos), status, backend,
carga_id (rótulo que o gerador de carga manda no cabeçalho X-Carga-Id), limite. Parâmetro inválido = 400.
"""

import csv
import io
import json
import math
from datetime import datetime

import numpy as np
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

import drift
import monitoramento


def arquivos_log() -> list:
    """Do mais antigo para o mais novo: inferencia.jsonl.5 ... .1 e por fim inferencia.jsonl."""
    base = monitoramento.ARQUIVO_LOG
    rotacionados = sorted(base.parent.glob(base.name + ".*"), key=lambda p: int(p.suffix[1:]) if p.suffix[1:].isdigit() else 0, reverse=True)
    return [p for p in [*rotacionados, base] if p.is_file()]


def ler_eventos() -> list[dict]:
    eventos = []
    for arquivo in arquivos_log():
        with arquivo.open(encoding="utf-8") as linhas:
            for linha in linhas:
                try:
                    eventos.append(json.loads(linha))
                except json.JSONDecodeError:
                    continue  # ex.: linha incompleta se o processo morreu no meio da escrita
    return eventos


def achatar(evento: dict) -> dict:
    """{"entrada": {"media_l": 1}} vira {"entrada.media_l": 1}; listas viram colunas ou texto."""
    plano = {}
    for chave, valor in evento.items():
        if isinstance(valor, dict):
            for sub, v in valor.items():
                if sub == "hist_l":
                    plano.update({f"{chave}.hist_l_{i:02d}": x for i, x in enumerate(v)})
                elif isinstance(v, list):
                    plano[f"{chave}.{sub}"] = "|".join(str(x) for x in v)
                else:
                    plano[f"{chave}.{sub}"] = v
        elif valor is not None or chave not in ("entrada", "saida"):
            plano[chave] = valor
    return plano


def _instante(texto: str | None) -> float | None:
    if not texto:
        return None
    quando = datetime.fromisoformat(texto)
    return (quando if quando.tzinfo else quando.astimezone()).timestamp()


def filtrar(parametros) -> list[dict]:
    """Aplica os filtros da query string e devolve as linhas achatadas."""
    inicio, fim = _instante(parametros.get("inicio")), _instante(parametros.get("fim"))
    evento = parametros.get("evento", "resposta")
    linhas = []
    for e in ler_eventos():
        if evento != "todos" and e.get("evento") != evento:
            continue
        if inicio is not None and e["ts_epoch"] < inicio or fim is not None and e["ts_epoch"] > fim:
            continue
        if parametros.get("status") and e.get("status") != parametros["status"]:
            continue
        if parametros.get("backend") and e.get("backend") != parametros["backend"]:
            continue
        if parametros.get("carga_id") and e.get("carga_id") != parametros["carga_id"]:
            continue
        linhas.append(achatar(e))
    if parametros.get("evento", "resposta") not in ("resposta", "pedido", "todos"):
        raise ValueError("evento deve ser resposta, pedido ou todos")
    limite = int(parametros.get("limite") or 0)
    if limite < 0:
        raise ValueError("limite não pode ser negativo")
    return linhas[-limite:] if limite > 0 else linhas


def _erro_parametro(erro: Exception) -> JSONResponse:
    return JSONResponse({"error": f"parâmetro inválido: {erro}"}, status_code=400)


async def variaveis(request: Request) -> Response:
    try:
        linhas = filtrar(request.query_params)
    except ValueError as erro:
        return _erro_parametro(erro)
    if request.query_params.get("formato", "json") == "csv":
        colunas = list(dict.fromkeys(c for linha in linhas for c in linha))  # união, na ordem em que aparecem
        saida = io.StringIO()
        escritor = csv.DictWriter(saida, fieldnames=colunas)
        escritor.writeheader()
        escritor.writerows(linhas)
        return PlainTextResponse(saida.getvalue(), media_type="text/csv; charset=utf-8")
    return JSONResponse({"n": len(linhas), "linhas": linhas})


def _numerico(valores: list) -> list[float]:
    return [float(v) for v in valores if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v))]


async def resumo(request: Request) -> Response:
    try:
        linhas = filtrar(request.query_params)
        faixas = int(request.query_params.get("faixas") or 10)
        if not 1 <= faixas <= 1000:
            raise ValueError("faixas deve estar entre 1 e 1000")
    except ValueError as erro:
        return _erro_parametro(erro)
    contagem_status = {}
    for linha in linhas:
        contagem_status[linha.get("status")] = contagem_status.get(linha.get("status"), 0) + 1
    por_variavel = {}
    colunas = dict.fromkeys(c for linha in linhas for c in linha)
    for coluna in colunas:
        if coluna.startswith("entrada.hist_l_") or coluna in ("ts_epoch", "versao_log", "n_respostas"):
            continue
        valores = _numerico([linha.get(coluna) for linha in linhas])
        if not valores:
            continue
        x = np.asarray(valores)
        contagens, limites = np.histogram(x, bins=faixas)
        por_variavel[coluna] = {
            "n": len(valores),
            "min": float(x.min()),
            "q1": float(np.quantile(x, 0.25)),
            "mediana": float(np.median(x)),
            "q3": float(np.quantile(x, 0.75)),
            "p95": float(np.quantile(x, 0.95)),
            "max": float(x.max()),
            "media": float(x.mean()),
            "histograma": {"limites": [round(float(v), 6) for v in limites], "contagens": contagens.tolist()},
        }
    return JSONResponse({"n": len(linhas), "por_status": contagem_status, "variaveis": por_variavel})


async def comparar_drift(request: Request) -> Response:
    try:
        parametros = dict(request.query_params)
        parametros["evento"] = "resposta"
        parametros["status"] = "ok"  # só pedidos que chegaram ao modelo têm entrada e saída completas
        alfa = float(request.query_params.get("alfa") or 0.05)
        if not 0 < alfa < 1:
            raise ValueError("alfa deve estar entre 0 e 1")
    except ValueError as erro:
        return _erro_parametro(erro)
    if not drift.ARQUIVO_REFERENCIA.is_file():
        return JSONResponse({"error": "monitoring/referencia.json não existe (rode: just referencia)"}, status_code=404)
    referencia = drift.carregar_referencia()
    # Compara só com o backend da referência (a nossa é do simulado): misturar com respostas do modelo
    # real acusaria "drift" que é só troca de backend. ?backend=ollama compara mesmo assim, de propósito.
    parametros.setdefault("backend", referencia.get("backend") or "")
    try:
        linhas = filtrar(parametros)
    except ValueError as erro:
        return _erro_parametro(erro)
    resultado = drift.comparar(linhas, referencia, alfa=alfa)
    resultado["backend_lote"] = parametros["backend"] or "todos"
    return JSONResponse(resultado)


async def referencia(_: Request) -> Response:
    if not drift.ARQUIVO_REFERENCIA.is_file():
        return JSONResponse({"error": "monitoring/referencia.json não existe (rode: just referencia)"}, status_code=404)
    return JSONResponse(drift.carregar_referencia())


app = Starlette(
    routes=[
        Route("/variaveis", variaveis),
        Route("/resumo", resumo),
        Route("/drift", comparar_drift),
        Route("/referencia", referencia),
    ]
)
