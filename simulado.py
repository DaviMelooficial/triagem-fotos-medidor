"""Backend simulado: substitui SÓ a chamada ao modelo, para gerar carga de monitoramento sem GPU.

Por que existe: numa máquina sem GPU uma chamada do qwen3-vl:4b leva de 194 a 286 s (medido), então
não dá para gerar centenas de pedidos e ver o rastro evoluir. Com BACKEND=simulado o serviço percorre
o mesmo caminho (validação da foto, variáveis de entrada, votação, log e métricas) e só esta função
troca o Ollama por uma resposta inventada. Cada linha do log diz qual backend respondeu.

Como a resposta é inventada (determinística por foto e seed, latência sorteada):
- os valores "verdadeiros" (número, função, leitura) saem do sha256 da foto: a mesma foto dá sempre a mesma base;
- uma nota de qualidade da foto (brilho, contraste, nitidez) controla a chance de cada chamada errar um campo,
  devolver null ou "alucinar" uma sequência (000000, 0123456789), como o modelo real faz em foto ruim.
  Assim a saída muda quando a entrada muda (foto escura ou borrada vai mais para revisão) e os
  histogramas de saída não ficam degenerados;
- a latência de cada chamada é sorteada de uma log-normal com mediana SIMULADO_LATENCIA_S (padrão 0,3 s),
  um pouco maior para fotos maiores, como no modelo real.
"""

import hashlib
import io
import math
import os
import random
import time

import numpy as np
from PIL import Image

MODELO = "simulado"
LATENCIA_MEDIANA_S = float(os.environ.get("SIMULADO_LATENCIA_S") or 0.3)
FUNCOES = ["03", "103", "kWh", "04", "kvarh"]
ALUCINACOES = ["000000", "0123456789", "8888888", "012345"]


def qualidade(imagem: bytes) -> tuple[float, float]:
    """Nota de 0 (péssima) a 1 (boa) e megapixels, calculadas numa cópia de 256 px (rápido)."""
    with Image.open(io.BytesIO(imagem)) as foto:
        megapixels = foto.width * foto.height / 1e6
        pequena = foto.convert("L")
        pequena.thumbnail((256, 256))
        luz = np.asarray(pequena, dtype=np.float32)
    brilho = 1 - min(abs(luz.mean() - 128) / 110, 1)  # muito escura ou estourada perde nota
    contraste = min(luz.std() / 45, 1)
    # Variância do Laplaciano (4 vizinhos): foto borrada tem pouca variação de borda.
    lap = luz[1:-1, :-2] + luz[1:-1, 2:] + luz[:-2, 1:-1] + luz[2:, 1:-1] - 4 * luz[1:-1, 1:-1]
    nitidez = min(lap.var() / 400, 1)
    return float(0.35 * brilho + 0.3 * contraste + 0.35 * nitidez), megapixels


def _digitos(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def perguntar(imagem: bytes, seed: int, modelo: str = MODELO) -> dict:
    """Mesmo contrato de extrator.perguntar: devolve um dict com os 3 campos (str ou None)."""
    nota, megapixels = qualidade(imagem)
    base = hashlib.sha256(imagem).hexdigest()
    verdade = random.Random(base)  # igual para todas as seeds: é o "medidor da foto"
    certo = {
        "numero_medidor": _digitos(verdade, 10),
        "funcao": verdade.choice(FUNCOES),
        "leitura": _digitos(verdade, verdade.choice([5, 6])),
    }
    rng = random.Random(f"{base}:{seed}")  # muda com a seed, como a temperatura do modelo real
    chance_erro = 0.03 + 0.75 * (1 - nota) ** 1.5
    resposta = {}
    for campo, valor in certo.items():
        sorteio = rng.random()
        if sorteio < chance_erro * 0.25:
            resposta[campo] = None  # "não visível"
        elif sorteio < chance_erro * 0.45 and campo == "leitura":
            resposta[campo] = rng.choice(ALUCINACOES)
        elif sorteio < chance_erro:
            pos = rng.randrange(len(valor)) if campo != "funcao" else 0
            trocado = rng.choice(FUNCOES) if campo == "funcao" else valor[:pos] + str(rng.randint(0, 9)) + valor[pos + 1 :]
            resposta[campo] = trocado
        else:
            resposta[campo] = valor
    # Latência: log-normal (cauda à direita, como tempo de inferência real); não depende da seed.
    mediana = LATENCIA_MEDIANA_S * (1 + 0.1 * megapixels)
    time.sleep(random.lognormvariate(math.log(mediana), 0.35))
    return resposta
