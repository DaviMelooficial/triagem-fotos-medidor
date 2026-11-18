"""Lê a foto de um medidor com um modelo de visão do Ollama e calcula a confiança por votação."""

import os
import time
from collections import Counter
from pathlib import Path

import ollama
from pydantic import BaseModel

# Configuração por variável de ambiente: troca o modelo ou o N sem mexer no código.
MODELO = os.environ.get("OLLAMA_MODELO") or "qwen3-vl:4b"  # escolhido pela avaliação (README)
N_RESPOSTAS = int(os.environ.get("N_RESPOSTAS") or 3)
if N_RESPOSTAS < 1 or N_RESPOSTAS % 2 == 0:  # ímpar evita empate na votação; 0 quebraria a conta
    raise ValueError(f"N_RESPOSTAS deve ser ímpar e maior que 0 (recebido: {N_RESPOSTAS})")
# "ollama" (padrão) chama o modelo de verdade; "simulado" troca só a chamada ao modelo por uma resposta
# derivada da foto (ver simulado.py), para gerar carga de monitoramento numa máquina sem GPU.
BACKEND = (os.environ.get("BACKEND") or "ollama").strip().lower()
if BACKEND not in ("ollama", "simulado"):
    raise ValueError(f"BACKEND deve ser 'ollama' ou 'simulado' (recebido: {BACKEND})")
# O prompt fica num arquivo de texto para poder ser ajustado sem editar Python.
PROMPT = (Path(__file__).parent / "prompt.txt").read_text(encoding="utf-8")
CAMPOS = ["numero_medidor", "funcao", "leitura"]


class Leitura(BaseModel):
    """Formato da resposta. O Ollama usa o JSON schema desta classe para forçar a saída."""

    numero_medidor: str | None
    funcao: str | None
    leitura: str | None


def perguntar(imagem: bytes, seed: int, modelo: str) -> dict:
    """Faz UMA pergunta ao modelo. A seed muda a cada chamada para as respostas variarem."""
    resposta = ollama.chat(
        model=modelo,
        messages=[{"role": "user", "content": PROMPT, "images": [imagem]}],
        format=Leitura.model_json_schema(),  # o modelo só pode devolver JSON nesse formato
        options={"temperature": 0.6, "seed": seed},  # 0.6: varia um pouco, sem virar aleatório
        think=False,  # sem "raciocínio" longo: o Qwen3-VL levaria ~100 s por foto pensando
    )
    # No Ollama 0.32 o Qwen3-VL com think=False devolve o JSON no campo "thinking", não no "content".
    texto = resposta.message.content or resposta.message.thinking
    leitura = Leitura.model_validate_json(texto).model_dump()
    limpo = {}
    for campo, valor in leitura.items():
        valor = (valor or "").strip()
        # Vazio ou "null" escrito como texto (o modelo às vezes faz isso) contam como "não visível".
        limpo[campo] = None if valor.lower() in ("", "null", "none") else valor
    return limpo


def votar(respostas: list[dict]) -> tuple[dict, dict]:
    """Para cada campo fica o valor mais votado; a confiança é a fração de votos dele."""
    valores, confianca = {}, {}
    for campo in CAMPOS:
        # Em empate (ex.: 3 respostas diferentes) vence a que apareceu primeiro, a da seed 1.
        valor, votos = Counter(r[campo] for r in respostas).most_common(1)[0]
        valores[campo] = valor
        confianca[campo] = round(votos / len(respostas), 2)
    # A foto é tão confiável quanto o seu campo mais duvidoso.
    confianca["geral"] = min(confianca[campo] for campo in CAMPOS)
    return valores, confianca


def extrair(foto: str | Path | bytes, modelo: str = MODELO, n: int = N_RESPOSTAS, backend: str = BACKEND) -> dict:
    """Função pública: recebe caminho ou bytes da foto e devolve o dicionário de resposta."""
    imagem = foto if isinstance(foto, bytes) else Path(foto).read_bytes()
    if backend == "simulado":
        import simulado  # import aqui: o modo real não depende do simulador

        pergunta, modelo = simulado.perguntar, simulado.MODELO
    else:
        pergunta = perguntar
    inicio = time.perf_counter()
    respostas, tempos = [], []
    for i in range(n):
        inicio_chamada = time.perf_counter()
        respostas.append(pergunta(imagem, seed=i + 1, modelo=modelo))
        tempos.append(round(time.perf_counter() - inicio_chamada, 3))  # tempo de cada chamada, para o log
    valores, confianca = votar(respostas)
    # Vai para revisão humana se o modelo hesitou em algum campo ou não achou algum campo.
    precisa_revisao = confianca["geral"] < 1.0 or None in valores.values()
    return {
        **valores,
        "confianca": confianca,
        "precisa_revisao": precisa_revisao,
        "modelo": modelo,
        "backend": backend,
        "respostas_brutas": respostas,
        "tempo_segundos": round(time.perf_counter() - inicio, 2),
        "tempos_chamadas_segundos": tempos,
    }
