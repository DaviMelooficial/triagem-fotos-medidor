"""Serviço HTTP (BentoML): recebe a foto de um medidor e devolve a extração em JSON."""

from pathlib import Path

import bentoml
import ollama
from bentoml.exceptions import InvalidArgument
from PIL import Image

import extrator


# timeout alto: cada foto faz N_RESPOSTAS chamadas ao modelo e isso pode levar dezenas de segundos.
@bentoml.service(traffic={"timeout": 300})
class TriagemMedidor:
    def __init__(self):
        # Checa na subida, e não na primeira requisição, para o erro aparecer logo e com a solução.
        try:
            instalados = [m.model for m in ollama.list().models]
        except Exception as erro:
            raise RuntimeError("Ollama não respondeu. Abra o app do Ollama ou rode: ollama serve") from erro
        if extrator.MODELO not in instalados:
            raise RuntimeError(f"Modelo {extrator.MODELO} não encontrado. Rode: ollama pull {extrator.MODELO}")

    @bentoml.api
    def extrair(self, foto: Path) -> dict:
        """Recebe a foto (multipart, campo "foto") e devolve número, função, leitura e confiança."""
        try:
            with Image.open(foto) as imagem:
                imagem.verify()  # lê o cabeçalho e confere se o arquivo é mesmo uma imagem
        except Exception:
            # InvalidArgument vira HTTP 400: o erro é de quem enviou, não do servidor.
            raise InvalidArgument("O arquivo enviado não é uma imagem válida. Envie uma foto JPEG ou PNG.")
        return extrator.extrair(foto)
