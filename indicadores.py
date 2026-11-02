#!/usr/bin/env python3
"""
Indicadores do Modelo 3C por caso e semestre (Fase 5, sem rede).

Le as tabelas de dados_processados/ (geradas por preprocessamento.py) e
calcula, para cada item (issue ou PR) aberto por uma pessoa, os indicadores
da Tabela tab:mapeamento3c da metodologia:
    - comunicacao: comentarios, participantes, tempo ate a primeira resposta;
    - coordenacao: rotulos, responsaveis, metas, pedidos de revisao,
      vereditos, revisao de outra pessoa, tempo ate a integracao;
    - cooperacao: PRs integrados, comentarios nas linhas, sugestoes de codigo;
    - percepcao: mencoes, referencias e PRs vinculados a issues.

Grava em indicadores/ (so contagens e proporcoes, pode ir para o git):
    - indicadores_semestre.csv: caso x semestre x tipo de item;
    - indicadores_periodo.csv: caso x periodo (todo o periodo e antes/depois
      do marco da automacao do agenda, aplicado a todos os casos como
      referencia temporal) x tipo de item.

Bots nao contam como autores, comentaristas, revisores nem participantes.
Nos PRs da automacao do agenda, o autor e quem abriu a issue (autor_efetivo).

COMO USAR
    python indicadores.py
"""

import csv
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

# ---------------------------------------------------------------------------
# CONFIGURACAO
# ---------------------------------------------------------------------------
ENTRADA = Path("dados_processados")
SAIDA = Path("indicadores")

# Iguais a preprocessamento.py (nao importado para nao depender de requests).
AGENDA = "agenda-tech-brasil/agenda-tech-brasil"
INICIO_AUTOMACAO_AGENDA = "2024-11-29"

# Mencao no texto ja pseudonimizado: @P0001 ou @[usuario] (login desconhecido).
RE_MENCAO = re.compile(r"@(P\d+|\[usuario\])")
RE_SUGESTAO = re.compile(r"```suggestion")

csv.field_size_limit(10**9)


# ---------------------------------------------------------------------------
# LEITURA
# ---------------------------------------------------------------------------
def ler(tabela):
    with open(ENTRADA / f"{tabela}.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def sim(valor):
    return valor == "True"


def horas(inicio, fim):
    f = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(fim, f) - datetime.strptime(inicio, f)).total_seconds() / 3600


def periodo(data):
    return "antes" if data[:10] < INICIO_AUTOMACAO_AGENDA else "depois"


# ---------------------------------------------------------------------------
# ITENS
# ---------------------------------------------------------------------------
def montar_itens():
    """Um dicionario por issue ou PR, com as interacoes de pessoas."""
    itens = {}
    for i in ler("issues"):
        if sim(i["eh_bot"]):
            continue
        itens[(i["repo"], i["numero"])] = {
            "repo": i["repo"], "tipo": "issue", "autor": i["autor"],
            "criado_em": i["criado_em"], "semestre": i["semestre"],
            "rotulo": bool(i["labels"]), "responsavel": bool(i["assignees"]),
            "meta": bool(i["milestone"]), "referencia": bool(i["referencias"]),
            "mencao": bool(RE_MENCAO.search(i["corpo"])),
        }
    for p in ler("pull_requests"):
        if sim(p["autor_eh_bot"]):
            continue
        itens[(p["repo"], p["numero"])] = {
            "repo": p["repo"], "tipo": "pr", "autor": p["autor_efetivo"],
            "criado_em": p["criado_em"], "semestre": p["semestre"],
            "rotulo": bool(p["labels"]), "responsavel": False, "meta": False,
            "referencia": bool(p["referencias"]),
            "mencao": bool(RE_MENCAO.search(p["corpo"])),
            "integrado": sim(p["foi_integrado"]),
            "integrado_em": p["integrado_em"],
            "vinculado": bool(p["issues_vinculadas"] or p["issue_origem"]),
            "revisado_por_outro": sim(p["revisado_por_outro"]),
            "auto_integrado": sim(p["auto_integrado_sem_revisao"]),
        }
    for it in itens.values():
        it.update(comentarios=0, respostas=[], participantes={it["autor"]},
                  pedido_revisao=False, pedido_mudancas=False,
                  comentario_linha=False, sugestao=False)

    def interacao(chave, autor, data, texto):
        it = itens.get(chave)
        if it is None:
            return None
        it["participantes"].add(autor)
        if autor != it["autor"]:
            it["respostas"].append(data)
        if RE_MENCAO.search(texto):
            it["mencao"] = True
        return it

    for c in ler("comentarios_issues"):
        if sim(c["eh_bot"]):
            continue
        it = interacao((c["repo"], c["numero_item"]), c["autor"], c["criado_em"], c["texto"])
        if it:
            it["comentarios"] += 1
    for r in ler("revisoes"):
        if sim(r["eh_bot"]):
            continue
        it = interacao((r["repo"], r["numero_pr"]), r["revisor"], r["enviado_em"], r["texto"])
        if it and r["revisor"] != it["autor"] and r["veredito"] == "CHANGES_REQUESTED":
            it["pedido_mudancas"] = True
    for c in ler("comentarios_revisao"):
        if sim(c["eh_bot"]):
            continue
        it = interacao((c["repo"], c["numero_pr"]), c["autor"], c["criado_em"], c["texto"])
        if it and c["autor"] != it["autor"]:
            it["comentario_linha"] = True
            if RE_SUGESTAO.search(c["texto"]):
                it["sugestao"] = True
    for e in ler("eventos"):
        it = itens.get((e["repo"], e["numero_item"]))
        if it is None or sim(e["eh_bot"]):
            continue
        if e["evento"] == "review_requested":
            it["pedido_revisao"] = True
        elif e["evento"] == "assigned":
            it["responsavel"] = True
        elif e["evento"] == "milestoned":
            it["meta"] = True
    return list(itens.values())


# ---------------------------------------------------------------------------
# INDICADORES
# ---------------------------------------------------------------------------
def prop(itens, campo):
    return round(sum(1 for i in itens if i[campo]) / len(itens), 3) if itens else ""


def mediana(valores):
    return round(median(valores), 1) if valores else ""


def indicadores(itens):
    """Indicadores de um grupo de itens do mesmo tipo."""
    com_resposta = [i for i in itens if i["respostas"]]
    linha = {
        "itens": len(itens),
        # Comunicacao
        "com_comentarios_por_item": round(sum(i["comentarios"] for i in itens) / len(itens), 2),
        "com_participantes_por_item": round(sum(len(i["participantes"]) for i in itens) / len(itens), 2),
        "com_prop_com_resposta": round(len(com_resposta) / len(itens), 3),
        "com_mediana_horas_1a_resposta": mediana(
            [horas(i["criado_em"], min(i["respostas"])) for i in com_resposta]),
        # Coordenacao
        "coo_prop_rotulo": prop(itens, "rotulo"),
        "coo_prop_responsavel": prop(itens, "responsavel"),
        "coo_prop_meta": prop(itens, "meta"),
        # Percepcao
        "per_prop_mencao": prop(itens, "mencao"),
        "per_prop_referencia": prop(itens, "referencia"),
    }
    if itens[0]["tipo"] == "pr":
        integrados = [i for i in itens if i["integrado"]]
        linha.update({
            "coo_prop_pedido_revisao": prop(itens, "pedido_revisao"),
            "coo_prop_pedido_mudancas": prop(itens, "pedido_mudancas"),
            "coo_integrados_prop_revisados_por_outro": prop(integrados, "revisado_por_outro"),
            "coo_integrados_prop_auto_sem_revisao": prop(integrados, "auto_integrado"),
            "coo_mediana_horas_ate_integracao": mediana(
                [horas(i["criado_em"], i["integrado_em"]) for i in integrados]),
            "cop_prs_integrados": len(integrados),
            "cop_autores_de_integrados": len({i["autor"] for i in integrados}),
            "cop_prop_comentario_linha": prop(itens, "comentario_linha"),
            "cop_prop_sugestao": prop(itens, "sugestao"),
            "per_prop_vinculado_issue": prop(itens, "vinculado"),
        })
    return linha


def agrupar(itens, chave):
    grupos = defaultdict(list)
    for i in itens:
        grupos[chave(i)].append(i)
    return grupos


def gravar(nome, linhas):
    SAIDA.mkdir(exist_ok=True)
    colunas = []
    for l in linhas:
        colunas += [c for c in l if c not in colunas]
    with open(SAIDA / nome, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colunas)
        w.writeheader()
        w.writerows(linhas)
    print(f"  salvo: {SAIDA / nome} ({len(linhas)} linhas)")


def main():
    itens = montar_itens()
    print(f"itens de autoria humana: {len(itens)}")

    por_semestre = agrupar(itens, lambda i: (i["repo"], i["semestre"], i["tipo"]))
    gravar("indicadores_semestre.csv", [
        {"repo": r, "semestre": s, "tipo": t, **indicadores(g)}
        for (r, s, t), g in sorted(por_semestre.items())])

    linhas = []
    for (r, t), g in sorted(agrupar(itens, lambda i: (i["repo"], i["tipo"])).items()):
        linhas.append({"repo": r, "periodo": "total", "tipo": t, **indicadores(g)})
        for p, gp in sorted(agrupar(g, lambda i: periodo(i["criado_em"])).items()):
            linhas.append({"repo": r, "periodo": p, "tipo": t, **indicadores(gp)})
    gravar("indicadores_periodo.csv", linhas)
    print(f"periodos antes/depois: marco {INICIO_AUTOMACAO_AGENDA} "
          f"(automacao do {AGENDA.split('/')[1]})")


if __name__ == "__main__":
    main()
