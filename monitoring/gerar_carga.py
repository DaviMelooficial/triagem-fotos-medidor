"""Gera carga no /extrair com as fotos de exemplos/ e variações feitas em memória (monitoring/variacoes.py).

Perfis (--perfil):
  normal  variações leves, como a referência: KS/PSI NÃO devem acusar drift na entrada
  drift   fotos escuras, estouradas, borradas, P&B, pequenas, giradas: KS/PSI devem acusar
  misto   70 % normal + 30 % drift (padrão)
Em qualquer perfil, --invalidos (padrão 0,1) é a fração de arquivos que devem dar 400.
Cada pedido leva o cabeçalho X-Carga-Id (padrão: <perfil>-<seed>), que o serviço grava no log como carga_id:
a análise e o /monitor filtram por ele, sem depender de horário.

Uso: uv run python monitoring/gerar_carga.py --n 100 [--perfil misto] [--url http://localhost:3000] [--paralelo 2]
     (ou: just carga 100)
"""

import argparse
import random
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx

import variacoes


def montar_pedidos(n: int, perfil: str, invalidos: float, seed: int) -> list[tuple[str, bytes, str]]:
    rng = random.Random(seed)
    base = variacoes.fotos_base()
    pedidos = []
    for i in range(n):
        nome, original = base[i % len(base)]
        if rng.random() < invalidos:
            tipo, conteudo, content_type = variacoes.invalido(original, rng)
            pedidos.append((f"invalido_{tipo}", conteudo, content_type))
            continue
        escolhido = perfil if perfil != "misto" else ("drift" if rng.random() < 0.3 else "normal")
        if escolhido == "drift":
            tipo, conteudo = variacoes.drift(original, rng)
            pedidos.append((f"drift_{tipo}", conteudo, "image/jpeg"))
        else:
            pedidos.append(("normal", variacoes.leve(original, rng), "image/jpeg"))
    return pedidos


def enviar(cliente: httpx.Client, url: str, pedido: tuple[str, bytes, str], carga_id: str) -> tuple[str, int, float]:
    tipo, conteudo, content_type = pedido
    # Nome genérico: o serviço nunca grava o nome do arquivo, mas não há por que mandar outro.
    extensao = content_type.split("/")[-1].replace("plain", "txt")
    inicio = time.perf_counter()
    try:
        resposta = cliente.post(
            f"{url}/extrair", files={"foto": (f"foto.{extensao}", conteudo, content_type)}, headers={"X-Carga-Id": carga_id}
        )
        status = resposta.status_code
    except httpx.HTTPError:
        status = 0  # sem resposta (timeout, conexão recusada)
    return tipo, status, time.perf_counter() - inicio


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--perfil", choices=["normal", "drift", "misto"], default="misto")
    parser.add_argument("--invalidos", type=float, default=0.1)
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--paralelo", type=int, default=2, help="pedidos simultâneos (o serviço atende 1 por vez; o resto espera na fila)")
    parser.add_argument("--seed", type=int, default=None, help="fixa a sequência de fotos (padrão: aleatória)")
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--carga-id", help="rótulo gravado no log (padrão: <perfil>-<seed>)")
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    carga_id = args.carga_id or f"{args.perfil}-{seed}"
    pedidos = montar_pedidos(args.n, args.perfil, args.invalidos, seed)
    print(f"Enviando {len(pedidos)} pedidos para {args.url}/extrair (perfil {args.perfil}, seed {seed}, {args.paralelo} em paralelo)")
    print(f"carga_id = {carga_id}  (filtre com: just analise {carga_id})")
    resultados = []
    inicio = time.perf_counter()
    with httpx.Client(timeout=args.timeout) as cliente, ThreadPoolExecutor(args.paralelo) as fila:
        for i, resultado in enumerate(fila.map(lambda p: enviar(cliente, args.url, p, carga_id), pedidos), start=1):
            resultados.append(resultado)
            if i % 10 == 0 or i == len(pedidos):
                print(f"  {i}/{len(pedidos)} enviados ({time.perf_counter() - inicio:.0f} s)", flush=True)
    status = Counter(s for _, s, _ in resultados)
    tipos = Counter(t for t, _, _ in resultados)
    tempos = sorted(t for _, s, t in resultados if s == 200)
    print("status HTTP:", dict(sorted(status.items())))
    print("tipos enviados:", dict(sorted(tipos.items())))
    if tempos:
        p95 = tempos[min(len(tempos) - 1, int(0.95 * len(tempos)))]
        print(f"latência dos 200 (cliente): mediana {statistics.median(tempos):.2f} s, p95 {p95:.2f} s, máx {tempos[-1]:.2f} s")
    if status.get(0):
        print(f"ATENÇÃO: {status[0]} pedidos sem resposta. O serviço está rodando em {args.url}?")


if __name__ == "__main__":
    main()
