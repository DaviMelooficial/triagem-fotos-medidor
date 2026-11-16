"""Monta o conjunto de referência (monitoring/referencia.json) contra o qual o log é comparado (KS e PSI).

Referência escolhida à mão: as 4 fotos de exemplos/ (tiradas pela equipe) com 50 variações LEVES cada
(brilho/contraste ±10 %, giro até 3°, recorte, escala, JPEG 80-95), seed fixa = 200 amostras reproduzíveis.
Representa "foto de campo aceitável". As variáveis de entrada são calculadas pela MESMA função do serviço
(monitoramento.variaveis_entrada); as de saída vêm do backend simulado, o mesmo usado na carga de teste.
Só números vão para o JSON: nenhuma foto é gravada.

Uso: uv run python monitoring/referencia.py   (ou: just referencia)
"""

import io
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path

os.environ["SIMULADO_LATENCIA_S"] = "0.001"  # aqui a latência não interessa: só a resposta
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from PIL import Image  # noqa: E402

import extrator  # noqa: E402
import monitoramento  # noqa: E402
import variacoes  # noqa: E402

VARIACOES_POR_FOTO = 50
SEED = 42
SAIDA = Path(__file__).resolve().parent / "referencia.json"
VARIAVEIS_ENTRADA = [
    "megapixels", "razao_aspecto", "bytes_por_pixel", "media_l", "media_r", "media_g", "media_b",
    "contraste_l", "croma_medio", "nitidez_laplaciano", "frac_escuros", "frac_estourados", "entropia_l",
]
VARIAVEIS_SAIDA = [
    "confianca_geral", "confianca_numero_medidor", "confianca_funcao", "confianca_leitura", "precisa_revisao",
    "n_campos_nulos", "leitura_suspeita", "respostas_identicas", "votos_distintos_leitura", "leitura_n_digitos", "funcao",
]


def main() -> None:
    rng = random.Random(SEED)
    valores = {f"entrada.{v}": [] for v in VARIAVEIS_ENTRADA} | {f"saida.{v}": [] for v in VARIAVEIS_SAIDA}
    hist_soma = [0.0] * monitoramento.BINS_HIST
    for _, original in variacoes.fotos_base():
        for _ in range(VARIACOES_POR_FOTO):
            bruto = variacoes.leve(original, rng)
            with Image.open(io.BytesIO(bruto)) as imagem:
                imagem.load()
                entrada = monitoramento.variaveis_entrada(imagem, bruto)
            saida = monitoramento.variaveis_saida(extrator.extrair(bruto, backend="simulado"))
            for v in VARIAVEIS_ENTRADA:
                valores[f"entrada.{v}"].append(entrada[v])
            for v in VARIAVEIS_SAIDA:
                valores[f"saida.{v}"].append(saida[v])  # como no log: número, booleano ou texto (função)
            hist_soma = [a + b for a, b in zip(hist_soma, entrada["hist_l"])]
    n = len(valores["entrada.media_l"])
    referencia = {
        "descricao": f"4 fotos de exemplos/ x {VARIACOES_POR_FOTO} variações leves (seed {SEED}); saída do backend simulado",
        "gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "backend": "simulado",  # o /monitor/drift compara só com linhas deste backend
        "n": n,
        "hist_l_soma": [round(x, 5) for x in hist_soma],
        "variaveis": valores,
    }
    SAIDA.write_text(json.dumps(referencia, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{SAIDA.relative_to(RAIZ)}: {n} amostras, {len(valores)} variáveis")


if __name__ == "__main__":
    main()
