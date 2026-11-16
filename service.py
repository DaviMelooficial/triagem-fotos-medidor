"""Serviço HTTP (BentoML): recebe a foto de um medidor e devolve a extração em JSON."""

import io
import os
from typing import Annotated

import bentoml
import ollama
from bentoml.exceptions import InvalidArgument
from PIL import Image
from pydantic import WrapValidator
from starlette.datastructures import UploadFile

import extrator

MENSAGEM_NAO_IMAGEM = "O arquivo enviado não é uma imagem válida. Envie uma foto JPEG ou PNG."


def abrir_foto(valor, proximo):
    """Abre o upload em memória. Com `Path` o BentoML gravava o arquivo em disco usando o nome que o
    cliente mandou (nome com mais de 255 bytes dava erro 500 antes do nosso código rodar).

    Abrimos a imagem aqui, e não no conversor padrão do BentoML, porque ele devolve 500 para arquivo
    que não é imagem e para "Content-Type: image/jpg"; aqui o erro vira 400 com mensagem clara.
    """
    if isinstance(valor, UploadFile):
        bruto = valor.file.read()
        try:
            with Image.open(io.BytesIO(bruto)) as teste:
                teste.verify()  # confere a estrutura do arquivo
            valor = Image.open(io.BytesIO(bruto))
            valor.load()  # decodifica de verdade: pega também foto cortada no meio
        except Exception:
            # InvalidArgument vira HTTP 400: o erro é de quem enviou, não do servidor.
            raise InvalidArgument(MENSAGEM_NAO_IMAGEM) from None
        valor.bytes_enviados = bruto  # os bytes originais vão para o modelo
    elif isinstance(valor, Image.Image) and not hasattr(valor, "bytes_enviados"):
        buffer = io.BytesIO()  # imagem já aberta (chamada direta em Python): gera os bytes
        valor.save(buffer, format=valor.format or "PNG")
        valor.bytes_enviados = buffer.getvalue()
    return proximo(valor)


Foto = Annotated[Image.Image, WrapValidator(abrir_foto)]

# timeout: cada foto faz N_RESPOSTAS chamadas ao modelo. Em GPU são segundos; em CPU uma chamada
# leva 194-286 s (medido), então sem GPU use N_RESPOSTAS=1 e, se preciso, TIMEOUT_SEGUNDOS=900.
TIMEOUT = int(os.environ.get("TIMEOUT_SEGUNDOS") or 300)


@bentoml.service(traffic={"timeout": TIMEOUT})
class TriagemMedidor:
    def __init__(self):
        if extrator.BACKEND == "simulado":
            return  # modo de teste de carga: não depende do Ollama
        # Checa na subida, e não na primeira requisição, para o erro aparecer logo e com a solução.
        try:
            instalados = [m.model for m in ollama.list().models]
        except Exception as erro:
            raise RuntimeError("Ollama não respondeu. Abra o app do Ollama ou rode: ollama serve") from erro
        if extrator.MODELO not in instalados:
            raise RuntimeError(f"Modelo {extrator.MODELO} não encontrado. Rode: ollama pull {extrator.MODELO}")

    @bentoml.api
    def extrair(self, foto: Foto) -> dict:
        """Recebe a foto (multipart, campo "foto") e devolve número, função, leitura e confiança."""
        # Outros formatos (WebP, TIFF...) davam erro 500 no Ollama.
        if foto.format not in ("JPEG", "PNG"):
            raise InvalidArgument(f"Formato {foto.format} não aceito. Envie uma foto JPEG ou PNG.")
        return extrator.extrair(foto.bytes_enviados)
