"""Rastro de cada pedido ao /extrair: variáveis da foto (entrada), do resultado (saída) e métricas.

Como funciona, em uma frase: o middleware abre um "rastro" para cada pedido; o endpoint preenche a
entrada (e grava o evento "pedido" ANTES de chamar o modelo) e a saída; quando a resposta HTTP
termina, o middleware grava o evento "resposta" com status, latência e tudo o que o endpoint juntou.
Um pedido que nem chega ao endpoint (ex.: arquivo que não é imagem, recusado na validação) também
ganha o seu evento "resposta", com o erro.

O log é JSON Lines (um objeto por linha) num arquivo append-only, escrito pelo módulo logging com
RotatingFileHandler. Pasta, tamanho e número de backups vêm de variáveis de ambiente (abaixo).
Nunca gravamos a foto nem o nome do arquivo enviado; só números calculados dela e o sha256.
"""

import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
import prometheus_client
from PIL import Image
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse

VERSAO_LOG = 1  # sobe quando o formato da linha mudar, para a análise saber ler as duas versões
PASTA_LOG = Path(os.environ.get("LOG_DIR") or Path(__file__).parent / "logs")
ARQUIVO_LOG = PASTA_LOG / "inferencia.jsonl"
LOG_MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES") or 10 * 1024 * 1024)  # 10 MB por arquivo
LOG_BACKUPS = int(os.environ.get("LOG_BACKUPS") or 5)  # inferencia.jsonl.1 ... .5
ENDPOINTS_RASTREADOS = ("/extrair",)
CHAVE_SCOPE = "triagem_rastro"  # onde o rastro do pedido fica guardado no scope ASGI
LADO_MAXIMO = 1024  # variáveis de cor e nitidez são calculadas numa cópia com no máximo 1024 px no lado maior
BINS_HIST = 16  # histograma de luminância por foto, 16 faixas de 16 níveis (para KS/PSI por pixel)


# ---------------------------------------------------------------- arquivo de log


class FormatoJsonl(logging.Formatter):
    """Uma linha JSON por evento. A hora vem do próprio logging (record.created), em ISO 8601
    com microssegundos e fuso horário, ex.: 2026-11-16T20:15:03.123456-03:00."""

    def format(self, record: logging.LogRecord) -> str:
        quando = datetime.fromtimestamp(record.created).astimezone()
        linha = {"ts": quando.isoformat(timespec="microseconds"), "ts_epoch": round(record.created, 6)}
        linha.update(record.msg if isinstance(record.msg, dict) else {"mensagem": record.getMessage()})
        return json.dumps(linha, ensure_ascii=False, separators=(",", ":"))


def _criar_logger() -> logging.Logger:
    logger = logging.getLogger("triagem.rastro")
    if not logger.handlers:  # evita handler duplicado se o módulo for importado de novo
        PASTA_LOG.mkdir(parents=True, exist_ok=True)
        # mode="a": append-only; a rotação renomeia o arquivo cheio para .1 e abre um novo vazio.
        handler = RotatingFileHandler(
            ARQUIVO_LOG, mode="a", maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
        handler.setFormatter(FormatoJsonl())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # não mistura com o log de acesso do BentoML no terminal
    return logger


def registrar(evento: dict) -> None:
    # O arquivo só é aberto no primeiro evento: importar este módulo (scripts, testes) não cria logs/.
    _criar_logger().info(evento)


# ---------------------------------------------------------------- métricas próprias no /metrics

# Ficam ao lado das métricas que o BentoML já expõe (bentoml_service_request_*). O BentoML recomenda
# usar o prometheus_client direto (bentoml.metrics está obsoleto); ele cuida de juntá-las no /metrics.
METRICA_PREDICOES = prometheus_client.Counter(
    name="triagem_predicoes_total",
    documentation="Pedidos ao /extrair por backend, resultado (ok/erro_cliente/erro_servidor) e precisa_revisao",
    labelnames=["backend", "status", "precisa_revisao"],
)
METRICA_CONFIANCA = prometheus_client.Histogram(
    name="triagem_confianca_geral",
    documentation="Confiança geral (fração de votos do campo mais duvidoso) das respostas 200",
    labelnames=["backend"],
    buckets=(0.2, 0.34, 0.5, 0.67, 0.8, 0.99, 1.0),
)
METRICA_LATENCIA_MODELO = prometheus_client.Histogram(
    name="triagem_latencia_chamada_modelo_segundos",
    documentation="Duração de CADA chamada ao modelo (o /extrair faz N_RESPOSTAS chamadas)",
    labelnames=["backend"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)
METRICA_LUMINANCIA = prometheus_client.Histogram(
    name="triagem_entrada_luminancia_media",
    documentation="Luminância média (0-255) das fotos recebidas: sinal de drift de entrada no Prometheus",
    buckets=(32.0, 64.0, 96.0, 128.0, 160.0, 192.0, 224.0, 255.0),
)


# ---------------------------------------------------------------- variáveis da foto (entrada)


def variaveis_entrada(imagem: Image.Image, bruto: bytes) -> dict:
    """Números que descrevem a foto. Servem para histograma/boxplot e para KS/PSI contra a referência."""
    largura, altura = imagem.size
    copia = imagem.convert("RGB")
    if max(copia.size) > LADO_MAXIMO:
        copia.thumbnail((LADO_MAXIMO, LADO_MAXIMO))  # mesma escala para todas: nitidez comparável
    rgb = np.asarray(copia, dtype=np.float32)
    luz = np.asarray(copia.convert("L"), dtype=np.float32)
    lap = luz[1:-1, :-2] + luz[1:-1, 2:] + luz[:-2, 1:-1] + luz[2:, 1:-1] - 4 * luz[1:-1, 1:-1]
    contagem, _ = np.histogram(luz, bins=BINS_HIST, range=(0, 256))
    fracao = contagem / contagem.sum()
    com_massa = fracao[fracao > 0]
    return {
        "sha256": hashlib.sha256(bruto).hexdigest(),  # identifica foto repetida sem guardar a foto
        "bytes": len(bruto),
        "formato": imagem.format,
        "modo_cor": imagem.mode,
        "largura": largura,
        "altura": altura,
        "megapixels": round(largura * altura / 1e6, 4),
        "razao_aspecto": round(largura / altura, 4),
        "bytes_por_pixel": round(len(bruto) / (largura * altura), 4),  # compressão: foto lisa/borrada comprime mais
        "tem_exif": bool(imagem.info.get("exif")),
        "media_l": round(float(luz.mean()), 3),
        "media_r": round(float(rgb[..., 0].mean()), 3),
        "media_g": round(float(rgb[..., 1].mean()), 3),
        "media_b": round(float(rgb[..., 2].mean()), 3),
        "contraste_l": round(float(luz.std()), 3),
        "croma_medio": round(float((rgb.max(axis=2) - rgb.min(axis=2)).mean() / 255), 4),
        "nitidez_laplaciano": round(float(lap.var()), 2),
        "frac_escuros": round(float((luz < 32).mean()), 4),
        "frac_estourados": round(float((luz > 223).mean()), 4),
        "entropia_l": round(float(-(com_massa * np.log2(com_massa)).sum()), 4),
        "hist_l": [round(float(x), 5) for x in fracao],
    }


# ---------------------------------------------------------------- variáveis do resultado (saída)


def _sequencia_suspeita(leitura: str | None) -> bool:
    """Padrões de alucinação vistos na avaliação: 000000, 8888888, 0123456789, 012345."""
    if not leitura or not leitura.isdigit() or len(leitura) < 4:
        return False
    if len(set(leitura)) == 1:
        return True
    return all(int(b) - int(a) == 1 for a, b in zip(leitura, leitura[1:]))


def variaveis_saida(resultado: dict) -> dict:
    """Resumo do resultado sem dado pessoal: guardamos tamanhos e confianças, nunca o número lido."""
    respostas = resultado["respostas_brutas"]
    tempos = resultado["tempos_chamadas_segundos"]
    confianca = resultado["confianca"]
    campos = ["numero_medidor", "funcao", "leitura"]
    leitura = resultado["leitura"]
    return {
        "precisa_revisao": resultado["precisa_revisao"],
        "confianca_geral": confianca["geral"],
        "confianca_numero_medidor": confianca["numero_medidor"],
        "confianca_funcao": confianca["funcao"],
        "confianca_leitura": confianca["leitura"],
        "campos_nulos": [c for c in campos if resultado[c] is None],
        "n_campos_nulos": sum(resultado[c] is None for c in campos),
        "respostas_identicas": all(r == respostas[0] for r in respostas),
        "votos_distintos_leitura": len({r["leitura"] for r in respostas}),
        "funcao": resultado["funcao"],  # categoria curta (03, 103, kWh...), não identifica ninguém
        "leitura_n_digitos": len(leitura) if leitura else 0,
        "numero_medidor_n_caracteres": len(resultado["numero_medidor"] or ""),
        "leitura_suspeita": _sequencia_suspeita(leitura),
        "latencia_modelo_s": round(sum(tempos), 3),
        "latencia_chamada_media_s": round(sum(tempos) / len(tempos), 3),
        "latencia_chamada_max_s": max(tempos),
        "latencias_chamadas_s": tempos,
    }


def observar_resultado(rastro: dict, resultado: dict) -> None:
    """Guarda a saída no rastro e alimenta as métricas que dependem do resultado do modelo."""
    rastro["saida"] = variaveis_saida(resultado)
    backend = resultado["backend"]
    METRICA_CONFIANCA.labels(backend=backend).observe(resultado["confianca"]["geral"])
    for tempo in resultado["tempos_chamadas_segundos"]:
        METRICA_LATENCIA_MODELO.labels(backend=backend).observe(tempo)


# ---------------------------------------------------------------- middleware que fecha cada rastro


CARGA_ID_VALIDO = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
MENSAGEM_SEM_FOTO = 'Envie a foto como arquivo no campo multipart "foto" (ex.: curl -F "foto=@foto.jpg").'


async def _conferir_multipart(scope, corpo: bytes, cabecalhos: dict) -> str | None:
    """Recusa (400) multipart sem um ARQUIVO no campo "foto". Sem isso o BentoML dava 500: com o campo
    com outro nome ele tenta ler a foto como texto (UnicodeDecodeError), e com "foto" em texto ele trata
    o texto como URL e tenta baixar."""
    if not cabecalhos.get("content-type", "").startswith("multipart/form-data"):
        return None  # JSON e outros: o validador do service.py responde 400

    async def receber():
        return {"type": "http.request", "body": corpo, "more_body": False}

    try:
        formulario = await Request(scope, receber).form()
    except Exception:
        return MENSAGEM_SEM_FOTO  # multipart malformado
    try:
        foto = formulario.get("foto")
        return None if isinstance(foto, UploadFile) else MENSAGEM_SEM_FOTO
    finally:
        await formulario.close()


class RastroMiddleware:
    """Middleware ASGI: mede o pedido inteiro e grava o evento "resposta", inclusive em erro 400/500."""

    def __init__(self, app, backend: str, modelo: str, n_respostas: int):
        self.app = app
        self.fixos = {"backend": backend, "modelo": modelo, "n_respostas": n_respostas}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in ENDPOINTS_RASTREADOS:
            await self.app(scope, receive, send)
            return
        inicio = time.perf_counter()
        cabecalhos = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        carga = cabecalhos.get("x-carga-id", "")
        rastro = {
            "request_id": uuid.uuid4().hex,
            # rótulo opcional mandado pelo gerador de carga; só aceito se for um nome curto e simples
            "carga_id": carga if CARGA_ID_VALIDO.fullmatch(carga) else None,
            "entrada": None,
            "saida": None,
            "erro_tipo": None,
        }
        scope[CHAVE_SCOPE] = rastro  # o endpoint lê e preenche este mesmo dicionário
        resposta = {"status": 500, "corpo": b""}

        # Lê o corpo inteiro (o BentoML também lê tudo antes de validar): assim contamos os bytes
        # de verdade, mesmo sem Content-Length (upload chunked), e conferimos o campo "foto".
        corpo = bytearray()
        while True:
            mensagem = await receive()
            if mensagem["type"] != "http.request":
                break  # cliente desconectou
            corpo += mensagem.get("body", b"")
            if not mensagem.get("more_body", False):
                break
        rastro["corpo_bytes"] = len(corpo)
        entregue = False

        async def receber():
            nonlocal entregue
            if not entregue:
                entregue = True
                return {"type": "http.request", "body": bytes(corpo), "more_body": False}
            return await receive()

        async def enviar(mensagem):
            if mensagem["type"] == "http.response.start":
                resposta["status"] = mensagem["status"]
                # devolve o request_id ao cliente: com ele dá para achar a linha do log
                mensagem.setdefault("headers", []).append((b"x-request-id", rastro["request_id"].encode()))
            elif mensagem["type"] == "http.response.body" and resposta["status"] >= 400:
                resposta["corpo"] += mensagem.get("body", b"")[:2000]
            await send(mensagem)

        try:
            recusa = await _conferir_multipart(scope, bytes(corpo), cabecalhos)
            if recusa:
                rastro["erro_tipo"] = "campo_foto_ausente"
                await JSONResponse([{"error": recusa}], status_code=400)(scope, receber, enviar)
            else:
                await self.app(scope, receber, enviar)
        finally:
            self._fechar(rastro, scope, resposta, time.perf_counter() - inicio)

    def _fechar(self, rastro, scope, resposta, duracao):
        http = resposta["status"]
        status = "ok" if http < 400 else "erro_cliente" if http < 500 else "erro_servidor"
        erro_msg = None
        if http >= 400:
            try:
                corpo = json.loads(resposta["corpo"] or b"null")
                corpo = corpo[0] if isinstance(corpo, list) and corpo else corpo
                erro_msg = (corpo or {}).get("error")
            except (ValueError, AttributeError):
                erro_msg = resposta["corpo"][:300].decode("utf-8", "replace")
        saida = rastro["saida"]
        evento = {
            "evento": "resposta",
            "versao_log": VERSAO_LOG,
            "request_id": rastro["request_id"],
            "endpoint": scope["path"],
            **self.fixos,
            "http_status": http,
            "status": status,
            "erro_tipo": rastro["erro_tipo"] or (("validacao_upload" if http == 400 else "desconhecido") if http >= 400 else None),
            "erro_msg": erro_msg,
            "latencia_total_s": round(duracao, 4),
            "corpo_bytes": rastro["corpo_bytes"],
            "carga_id": rastro["carga_id"],
            "entrada": rastro["entrada"],
            "saida": saida,
        }
        registrar(evento)
        revisao = "sem_resultado" if saida is None else str(saida["precisa_revisao"]).lower()
        METRICA_PREDICOES.labels(backend=self.fixos["backend"], status=status, precisa_revisao=revisao).inc()
