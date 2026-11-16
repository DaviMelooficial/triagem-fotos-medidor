"""Variações sintéticas das fotos de exemplos/, feitas em memória (nenhuma foto nova é gravada em disco).

- leve:  o que se espera de fotos boas de campo: brilho e contraste ±10 %, giro de até 3°, recorte de
         até 8 %, escala 80-100 % e JPEG de qualidade 80-95. É o perfil da REFERÊNCIA e da carga "normal".
- drift: o que estraga a leitura: foto escura, estourada, borrada, em preto e branco, pequena
         (como as 360x480 de campo) ou girada 90°. É o perfil da carga "drift".
- invalido: arquivos que o serviço tem de recusar com 400 (texto, JPEG cortado, WebP, GIF).
"""

import io
import random
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

EXEMPLOS = Path(__file__).resolve().parent.parent / "exemplos"


def fotos_base() -> list[tuple[str, bytes]]:
    return [(p.name, p.read_bytes()) for p in sorted(EXEMPLOS.glob("*.jpg"))]


def _jpeg(imagem: Image.Image, qualidade: int) -> bytes:
    buffer = io.BytesIO()
    imagem.convert("RGB").save(buffer, format="JPEG", quality=qualidade)
    return buffer.getvalue()


def leve(original: bytes, rng: random.Random) -> bytes:
    with Image.open(io.BytesIO(original)) as foto:
        imagem = foto.convert("RGB")
    imagem = ImageEnhance.Brightness(imagem).enhance(rng.uniform(0.9, 1.1))
    imagem = ImageEnhance.Contrast(imagem).enhance(rng.uniform(0.9, 1.1))
    imagem = imagem.rotate(rng.uniform(-3, 3), resample=Image.BICUBIC, fillcolor=(128, 128, 128))
    corte = rng.uniform(0, 0.08)
    largura, altura = imagem.size
    imagem = imagem.crop((int(largura * corte / 2), int(altura * corte / 2), int(largura * (1 - corte / 2)), int(altura * (1 - corte / 2))))
    escala = rng.uniform(0.8, 1.0)
    imagem = imagem.resize((int(imagem.width * escala), int(imagem.height * escala)))
    return _jpeg(imagem, rng.randint(80, 95))


def drift(original: bytes, rng: random.Random) -> tuple[str, bytes]:
    with Image.open(io.BytesIO(original)) as foto:
        imagem = foto.convert("RGB")
    tipo = rng.choice(["escura", "estourada", "borrada", "preto_e_branco", "pequena", "girada_90"])
    if tipo == "escura":
        imagem = ImageEnhance.Brightness(imagem).enhance(rng.uniform(0.2, 0.45))
    elif tipo == "estourada":
        imagem = ImageEnhance.Brightness(imagem).enhance(rng.uniform(1.6, 2.2))
    elif tipo == "borrada":
        imagem = imagem.filter(ImageFilter.GaussianBlur(rng.uniform(3, 6)))
    elif tipo == "preto_e_branco":
        imagem = imagem.convert("L").convert("RGB")
    elif tipo == "pequena":
        imagem = imagem.resize((360, 480))
    else:
        imagem = imagem.rotate(90, expand=True)
    return tipo, _jpeg(imagem, rng.randint(60, 90))


def invalido(original: bytes, rng: random.Random) -> tuple[str, bytes, str]:
    """Devolve (tipo, conteúdo, content-type)."""
    tipo = rng.choice(["texto", "jpeg_cortado", "webp", "gif"])
    if tipo == "texto":
        return tipo, b"isto nao e uma foto", "text/plain"
    if tipo == "jpeg_cortado":
        return tipo, original[: len(original) // 3], "image/jpeg"
    with Image.open(io.BytesIO(original)) as foto:
        pequena = foto.convert("RGB").resize((90, 160))
    buffer = io.BytesIO()
    pequena.save(buffer, format="WEBP" if tipo == "webp" else "GIF")
    return tipo, buffer.getvalue(), f"image/{tipo}"
