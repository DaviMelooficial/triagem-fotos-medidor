"""Desenha fotos SINTÉTICAS de medidores (marcas e números fictícios) para testes e demonstração.

Nenhum pixel vem de foto real: tudo é desenhado aqui com Pillow. Rode: uv run python gerar_exemplos.py
"""

import csv
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PASTA = Path(__file__).parent / "exemplos"
LARGURA, ALTURA = 360, 480  # mesmo tamanho das fotos de campo

# Quais dos 7 segmentos acendem em cada dígito (a=topo, b=sup. dir., c=inf. dir., d=base,
# e=inf. esq., f=sup. esq., g=meio), como num display de calculadora.
SEGMENTOS = {
    "0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg",
    "5": "acdfg", "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg",
}


def fonte(tamanho):
    # Fonte embutida no Pillow: funciona igual em Mac, Linux e Windows, sem instalar nada.
    return ImageFont.load_default(size=tamanho)


def digito_7seg(draw, x, y, w, h, digito, cor):
    """Desenha um dígito de 7 segmentos como polígonos (cada segmento é um hexágono fino)."""
    t = max(2, w // 5)  # espessura do segmento
    meio = y + h // 2
    horizontais = {"a": y, "g": meio, "d": y + h}
    verticais = {"f": (x, y, meio), "b": (x + w, y, meio), "e": (x, meio, y + h), "c": (x + w, meio, y + h)}
    for seg in SEGMENTOS[digito]:
        if seg in horizontais:
            yy = horizontais[seg]
            pontos = [(x + 2, yy), (x + t, yy - t // 2), (x + w - t, yy - t // 2),
                      (x + w - 2, yy), (x + w - t, yy + t // 2), (x + t, yy + t // 2)]
        else:
            xx, y0, y1 = verticais[seg]
            pontos = [(xx, y0 + 2), (xx + t // 2, y0 + t), (xx + t // 2, y1 - t),
                      (xx, y1 - 2), (xx - t // 2, y1 - t), (xx - t // 2, y0 + t)]
        draw.polygon(pontos, fill=cor)


def codigo_de_barras(draw, x, y, largura, altura, semente):
    """Listras pretas de larguras aleatórias: parece código de barras, mas não codifica nada."""
    sorteio = random.Random(semente)
    fim = x + largura
    while x < fim:
        barra = sorteio.choice([1, 1, 2, 3])
        draw.rectangle([x, y, min(x + barra - 1, fim), y + altura], fill="black")
        x += barra + sorteio.choice([1, 2, 2, 3])  # barra + espaço branco


def medidor_eletronico(funcao, leitura, serie, marca):
    """Medidor eletrônico: display LCD esverdeado com função + leitura e etiqueta com série."""
    img = Image.new("RGB", (LARGURA, ALTURA), (96, 92, 84))  # parede ao fundo
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([12, 20, 348, 462], radius=28, fill=(205, 208, 204), outline=(70, 70, 70), width=6)
    d.text((180, 70), marca, font=fonte(30), fill=(60, 64, 70), anchor="mm")
    # Display LCD: fundo verde-acinzentado e dígitos escuros, função à esquerda separada da leitura.
    d.rectangle([60, 110, 300, 170], fill=(158, 178, 140), outline=(40, 40, 40), width=3)
    escuro = (35, 45, 35)
    for i, c in enumerate(funcao):
        digito_7seg(d, 74 + i * 22, 122, 14, 36, c, escuro)
    for i, c in enumerate(leitura):
        digito_7seg(d, 136 + i * 26, 122, 17, 36, c, escuro)
    d.text((180, 195), "CLASSE B   kWh   127 V   15(100) A", font=fonte(13), fill=(40, 40, 40), anchor="mm")
    d.text((180, 215), "MEDIDOR ELETRONICO MONOFASICO", font=fonte(13), fill=(40, 40, 40), anchor="mm")
    # Etiqueta da série: número impresso em cima do código de barras.
    d.rectangle([150, 270, 330, 360], fill=(240, 240, 236), outline=(90, 90, 90), width=2)
    d.text((240, 292), serie, font=fonte(24), fill="black", anchor="mm")
    codigo_de_barras(d, 162, 312, 156, 36, serie)
    d.rectangle([30, 270, 140, 360], outline=(60, 60, 60), width=2)  # diagrama de ligação
    for i in range(4):
        d.line([45 + i * 25, 285, 45 + i * 25, 345], fill=(60, 60, 60), width=2)
    return img


def medidor_rolete(kwh, kvarh, serie, marca):
    """Medidor de rolete (ciclométrico): dígitos brancos em caixinhas pretas, dois registros."""
    img = Image.new("RGB", (LARGURA, ALTURA), (150, 102, 66))  # fundo de madeira
    d = ImageDraw.Draw(img)
    d.rectangle([10, 50, 330, 430], fill=(226, 226, 220), outline=(80, 80, 80), width=5)
    d.text((170, 80), f"{marca}  MEDIDOR DE ENERGIA", font=fonte(16), fill=(40, 40, 40), anchor="mm")
    for topo, valor, unidade in [(120, kwh, "kWh"), (220, kvarh, "kvarh")]:
        d.rectangle([60, topo, 250, topo + 46], fill=(30, 30, 30))
        for i, c in enumerate(valor):
            d.rectangle([68 + i * 36, topo + 6, 96 + i * 36, topo + 40], fill="black", outline=(120, 120, 120))
            d.text((82 + i * 36, topo + 23), c, font=fonte(28), fill="white", anchor="mm")
        d.text((155, topo + 62), unidade, font=fonte(16), fill=(30, 30, 30), anchor="mm")
    d.rectangle([200, 330, 320, 400], fill=(245, 245, 240), outline=(90, 90, 90), width=2)
    d.text((260, 347), serie, font=fonte(20), fill="black", anchor="mm")
    codigo_de_barras(d, 208, 362, 104, 30, serie)
    return img


def foto_ruim(img):
    """Simula foto tirada às pressas: um pouco torta, desfocada e com ruído."""
    img = img.rotate(6, resample=Image.BICUBIC, fillcolor=(96, 92, 84))
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    # Ruído com seed fixa: rodar "just exemplos" de novo gera exatamente a mesma imagem.
    sorteio = random.Random(42)
    pixels = bytes(min(255, max(0, int(sorteio.gauss(128, 40)))) for _ in range(LARGURA * ALTURA))
    ruido = Image.frombytes("L", (LARGURA, ALTURA), pixels).convert("RGB")
    return Image.blend(img, ruido, 0.12)


def sem_medidor():
    """Caso negativo: caixa de metal fechada na parede, sem nenhum medidor visível."""
    img = Image.new("RGB", (LARGURA, ALTURA), (180, 172, 160))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 70, 300, 420], fill=(120, 124, 128), outline=(60, 60, 60), width=6)
    d.rectangle([270, 220, 290, 270], fill=(60, 60, 60))  # trinco
    d.text((180, 130), "CAIXA FECHADA", font=fonte(20), fill=(230, 230, 230), anchor="mm")
    return img


def main():
    PASTA.mkdir(exist_ok=True)
    # Cada linha: arquivo, imagem, e os valores fictícios que a imagem mostra (para o CSV).
    casos = [
        ("01_eletronico.jpg", medidor_eletronico("03", "052817", "4071835526", "MEDTEC"), "4071835526", "52817"),
        ("02_rolete.jpg", medidor_rolete("28504", "06281", "07291645", "VOLTARIS"), "07291645", "28504"),
        ("03_foto_ruim.jpg", foto_ruim(medidor_eletronico("03", "019364", "5823904417", "MEDTEC")), "5823904417", "19364"),
        ("04_sem_medidor.jpg", sem_medidor(), "6604128830", "NA"),
        ("05_alfanumerico.jpg", medidor_eletronico("03", "007452", "K90316", "VOLTARIS"), "K90316", "7452"),
    ]
    linhas = []
    for nome, img, serie, leitura in casos:
        img.save(PASTA / nome, quality=70)  # JPEG com qualidade baixa, como as fotos de campo
        linhas.append({"Numero do medidor": serie, "Posicao do medidor lida": leitura,
                       "Nota de Leitura Atual": "NA", "Foto do medidor": nome})
    # Mesmo esquema de colunas do lote real: separador ";" e "NA" quando não há valor.
    with open(PASTA / "controle_ficticio.csv", "w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=linhas[0].keys(), delimiter=";")
        escritor.writeheader()
        escritor.writerows(linhas)
    print(f"{len(casos)} imagens e controle_ficticio.csv gravados em {PASTA}")


if __name__ == "__main__":
    main()
