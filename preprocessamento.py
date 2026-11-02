#!/usr/bin/env python3
"""
Pre-processamento dos dados coletados por app.py (Fase 4).

Le os CSVs de <output_dir> e gera, em dados_processados/:
    - as tabelas de todos os casos consolidadas e pseudonimizadas
      (issues, pull_requests, revisoes, comentarios_issues,
      comentarios_revisao, eventos, commits), com marcacoes de analise:
      bot, semestre, outubro (Hacktoberfest), regime do agenda, autoria
      efetiva dos PRs da automacao e PRs auto-integrados sem revisao;
    - participantes.csv: uma linha por pessoa e caso, com contagens de
      atividade e as classificacoes nucleo/periferia.

O mapa login -> codigo fica em <output_dir>/privado/,
que nao deve sair da maquina de quem coletou. Os codigos sao estaveis: rodar de
novo reaproveita o mapa e so acrescenta pessoas novas.

COMO USAR
    python preprocessamento.py
"""

import csv
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from app import CONFIG

# ---------------------------------------------------------------------------
# CONFIGURACAO
# ---------------------------------------------------------------------------
SAIDA = Path("dados_processados")
PRIVADO = Path(CONFIG["output_dir"]) / "privado"

# Contas que a API registra como pessoa, mas sao automacoes (revisadas a mao).
BOTS_MANUAIS = {
    "sonarcloud",     # SonarCloud, so aparece mencionado/inscrito
    "bot-bughalla",   # so referencia commits
    "Factobot",       # abre issue de erro de endpoint; provavel monitoramento
}

# Agenda: data do primeiro PR aberto pela automacao de issue -> PR.
AGENDA = "agenda-tech-brasil/agenda-tech-brasil"
INICIO_AUTOMACAO_AGENDA = "2024-11-29"

# Semente da ordem aleatoria dos codigos P0001... (fixa para ser reproduzivel).
SEMENTE = 20261029

# Nucleo por atividade: menor grupo que soma esta fracao da atividade do caso.
FRACAO_NUCLEO = 0.8

# Associacoes do GitHub que indicam papel de mantenedor no repositorio.
ASSOC_NUCLEO = {"OWNER", "MEMBER", "COLLABORATOR"}

TABELAS = ["issues", "pull_requests", "revisoes", "comentarios_issues",
           "comentarios_revisao", "eventos", "commits"]

# Colunas com login (unico) e com lista de logins separados por "|".
COLUNAS_LOGIN = {
    "issues": ["autor"],
    "pull_requests": ["autor", "integrado_por", "autor_efetivo"],
    "revisoes": ["revisor"],
    "comentarios_issues": ["autor"],
    "comentarios_revisao": ["autor"],
    "eventos": ["ator", "responsavel", "revisor_solicitado"],
    "commits": ["autor", "committer"],
}
COLUNAS_LISTA = {"issues": ["assignees"]}
COLUNAS_TEXTO = {
    "issues": ["titulo", "corpo"],
    "pull_requests": ["titulo", "corpo"],
    "revisoes": ["texto"],
    "comentarios_issues": ["texto"],
    "comentarios_revisao": ["texto"],
    "commits": ["titulo"],
}


# ---------------------------------------------------------------------------
# AUXILIARES
# ---------------------------------------------------------------------------
def ler(repo, tabela):
    arquivo = Path(CONFIG["output_dir"]) / f"{repo.replace('/', '__')}__{tabela}.csv"
    with open(arquivo, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def salvar(arquivo, linhas, campos=None):
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    campos = campos or (list(linhas[0].keys()) if linhas else [])
    with open(arquivo, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(linhas)
    print(f"  salvo: {arquivo} ({len(linhas)} linhas)")


def eh_bot(login, tipo):
    return (tipo in ("Bot", "Organization") or login.endswith("[bot]")
            or login in BOTS_MANUAIS)


def semestre(data):
    return f"{data[:4]}.{1 if data[5:7] < '07' else 2}" if data else ""


def regime(repo, data):
    if repo != AGENDA or not data:
        return ""
    return "antes" if data[:10] < INICIO_AUTOMACAO_AGENDA else "depois"


def marcar_tempo(linha, repo, campo_data):
    data = linha.get(campo_data, "")
    linha["semestre"] = semestre(data)
    linha["outubro"] = data[5:7] == "10"
    linha["regime_agenda"] = regime(repo, data)


# ---------------------------------------------------------------------------
# PSEUDONIMIZACAO
# ---------------------------------------------------------------------------
class Pseudonimizador:
    """Troca logins por codigos estaveis (P0001...) e limpa os textos."""

    def __init__(self, arquivo_mapa):
        self.arquivo = arquivo_mapa
        self.mapa = {}
        if arquivo_mapa.exists():
            with open(arquivo_mapa, encoding="utf-8-sig", newline="") as f:
                self.mapa = {l["login"].lower(): l["codigo"] for l in csv.DictReader(f)}
        self._regex = None

    def registrar(self, logins):
        """Atribui codigos as pessoas novas, em ordem aleatoria (semente fixa)."""
        novos = sorted({l for l in logins if l and l.lower() not in self.mapa})
        random.Random(SEMENTE).shuffle(novos)
        proximo = len(self.mapa) + 1
        for login in novos:
            self.mapa[login.lower()] = f"P{proximo:04d}"
            proximo += 1
        self._regex = None

    def salvar(self):
        linhas = [{"login": l, "codigo": c} for l, c in sorted(self.mapa.items(), key=lambda x: x[1])]
        salvar(self.arquivo, linhas, ["login", "codigo"])

    def codigo(self, login):
        if not login:
            return "[conta removida]"
        return self.mapa.get(login.lower(), login)  # bots ficam com o nome

    def _padrao(self):
        if self._regex is None:
            # logins curtos demais geram trocas indevidas em palavras comuns
            nomes = sorted((l for l in self.mapa if len(l) >= 4), key=len, reverse=True)
            alternativas = "|".join(re.escape(n) for n in nomes) or r"(?!x)x"
            self._regex = re.compile(rf"(?<![\w-])({alternativas})(?![\w-])", re.IGNORECASE)
        return self._regex

    def texto(self, texto):
        if not texto:
            return texto
        texto = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", texto)
        # @mencoes: pessoa conhecida -> codigo; desconhecida -> marcador
        texto = re.sub(r"(?<![\w.])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))",
                       lambda m: "@" + self.mapa.get(m.group(1).lower(), "[usuario]")
                       if not m.group(1).lower().endswith("bot") else m.group(0), texto)
        # login citado sem @ (inclui URLs github.com/<login> e "from <login>/branch")
        return self._padrao().sub(lambda m: self.mapa[m.group(1).lower()], texto)


# ---------------------------------------------------------------------------
# ETAPAS
# ---------------------------------------------------------------------------
def carregar():
    dados = {t: [] for t in TABELAS}
    for repo in CONFIG["repos"]:
        for t in TABELAS:
            dados[t].extend(ler(repo, t))
    return dados


def preparar_pulls(dados):
    """Autoria efetiva, origem e auto-integracao sem revisao de cada PR."""
    autores_issue = {(l["repo"], l["numero"]): l["autor"] for l in dados["issues"]}
    revisores = defaultdict(set)
    for r in dados["revisoes"]:
        if not eh_bot(r["revisor"], r["tipo_revisor"]):
            revisores[(r["repo"], r["numero_pr"])].add(r["revisor"])

    for pr in dados["pull_requests"]:
        chave = (pr["repo"], pr["numero"])
        pr["issue_origem"] = ""
        pr["origem"] = "direta"
        pr["autor_efetivo"] = pr["autor"]
        pr["autor_eh_bot"] = eh_bot(pr["autor"], pr["tipo_autor"])
        if pr["repo"] == AGENDA and pr["autor"] == "github-actions[bot]":
            origem = (pr["issues_vinculadas"] or pr["referencias"]).split("|")[0]
            pr["origem"] = "automacao"
            pr["issue_origem"] = origem
            # issue fora dos dados (ex.: removida): autoria fica desconhecida
            pr["autor_efetivo"] = autores_issue.get((pr["repo"], origem), "")
            pr["autor_eh_bot"] = False
        outros = revisores[chave] - {pr["autor_efetivo"]}
        pr["revisado_por_outro"] = bool(outros)
        pr["auto_integrado_sem_revisao"] = (
            pr["foi_integrado"] == "True" and not outros
            and bool(pr["integrado_por"]) and pr["integrado_por"] == pr["autor_efetivo"])
        marcar_tempo(pr, pr["repo"], "criado_em")


def marcar_tabelas(dados):
    campos = {"issues": ("autor", "tipo_autor", "criado_em"),
              "revisoes": ("revisor", "tipo_revisor", "enviado_em"),
              "comentarios_issues": ("autor", "tipo_autor", "criado_em"),
              "comentarios_revisao": ("autor", "tipo_autor", "criado_em"),
              "eventos": ("ator", "tipo_ator", "criado_em"),
              "commits": ("autor", "tipo_autor", "data_commit")}
    for tabela, (c_login, c_tipo, c_data) in campos.items():
        for l in dados[tabela]:
            l["eh_bot"] = eh_bot(l[c_login], l[c_tipo])
            marcar_tempo(l, l["repo"], c_data)


def humanos_por_caso(dados):
    """{repo: {login: Counter de atividades}} so de pessoas (sem bots)."""
    atividade = defaultdict(lambda: defaultdict(Counter))
    datas = defaultdict(lambda: defaultdict(list))
    assoc = defaultdict(lambda: defaultdict(set))

    def somar(repo, login, tipo_ativ, data, associacao=""):
        if not login:
            return
        atividade[repo][login][tipo_ativ] += 1
        datas[repo][login].append(data)
        if associacao:
            assoc[repo][login].add(associacao)

    for l in dados["issues"]:
        if not l["eh_bot"]:
            somar(l["repo"], l["autor"], "issues", l["criado_em"], l["assoc_autor"])
    for l in dados["pull_requests"]:
        if not l["autor_eh_bot"]:
            # na automacao, a associacao registrada e a do bot; nao usar
            a = l["assoc_autor"] if l["origem"] == "direta" else ""
            somar(l["repo"], l["autor_efetivo"], "prs", l["criado_em"], a)
    for l in dados["revisoes"]:
        if not l["eh_bot"]:
            somar(l["repo"], l["revisor"], "revisoes", l["enviado_em"], l["assoc_revisor"])
    for t in ("comentarios_issues", "comentarios_revisao"):
        for l in dados[t]:
            if not l["eh_bot"]:
                somar(l["repo"], l["autor"], "comentarios", l["criado_em"], l["assoc_autor"])
    for l in dados["commits"]:
        if not l["eh_bot"]:
            somar(l["repo"], l["autor"], "commits", l["data_commit"])
    return atividade, datas, assoc


def tabela_participantes(atividade, datas, assoc, pseudo):
    """Uma linha por pessoa e caso."""
    ordem_assoc = ["OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR",
                   "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE"]
    linhas = []
    for repo, pessoas in atividade.items():
        # nucleo por atividade: menor grupo com FRACAO_NUCLEO das acoes
        # (PRs, revisoes e comentarios; commits ficam de fora por duplicarem PRs)
        peso = {p: c["prs"] + c["revisoes"] + c["comentarios"] for p, c in pessoas.items()}
        total = sum(peso.values())
        nucleo, acumulado = set(), 0
        for p, v in sorted(peso.items(), key=lambda x: -x[1]):
            if total and acumulado >= FRACAO_NUCLEO * total:
                break
            nucleo.add(p)
            acumulado += v
        for login, c in pessoas.items():
            a = sorted(assoc[repo][login], key=lambda x: ordem_assoc.index(x)
                       if x in ordem_assoc else len(ordem_assoc))
            maior = a[0] if a else ""
            contribuicoes = c["issues"] + c["prs"]
            linhas.append({
                "repo": repo,
                "participante": pseudo.codigo(login),
                "issues": c["issues"], "prs": c["prs"], "revisoes": c["revisoes"],
                "comentarios": c["comentarios"], "commits": c["commits"],
                "primeira_atividade": min(datas[repo][login]),
                "ultima_atividade": max(datas[repo][login]),
                "maior_associacao": maior,
                "nucleo_associacao": maior in ASSOC_NUCLEO,
                "nucleo_atividade": login in nucleo,
                # uma unica issue ou PR e nenhuma outra participacao
                "contribuicao_unica": contribuicoes == 1 and c["revisoes"] + c["comentarios"] == 0,
            })
    return linhas


# ---------------------------------------------------------------------------
# PRINCIPAL
# ---------------------------------------------------------------------------
def main():
    print("Lendo dados coletados...")
    dados = carregar()
    preparar_pulls(dados)
    marcar_tabelas(dados)

    print("Participantes...")
    atividade, datas, assoc = humanos_por_caso(dados)
    pseudo = Pseudonimizador(PRIVADO / "mapa_participantes.csv")
    todos = {login for pessoas in atividade.values() for login in pessoas}
    # pessoas que so aparecem em eventos, atribuicoes ou mencoes tambem precisam de codigo
    for t, colunas in COLUNAS_LOGIN.items():
        for l in dados[t]:
            for c in colunas:
                if l.get(c) and not eh_bot(l[c], l.get("tipo_" + c, "")):
                    todos.add(l[c])
    for l in dados["issues"]:
        todos.update(a for a in l["assignees"].split("|") if a)
    pseudo.registrar(todos - BOTS_MANUAIS - {l for l in todos if l.endswith("[bot]")})
    pseudo.salvar()
    participantes = tabela_participantes(atividade, datas, assoc, pseudo)

    print("Pseudonimizando e salvando...")
    for t in TABELAS:
        for l in dados[t]:
            for c in COLUNAS_LOGIN.get(t, []):
                if c in l:
                    l[c] = pseudo.codigo(l[c]) if l[c] else ""
            for c in COLUNAS_LISTA.get(t, []):
                l[c] = "|".join(pseudo.codigo(a) for a in l[c].split("|") if a)
            for c in COLUNAS_TEXTO.get(t, []):
                l[c] = pseudo.texto(l[c])
        salvar(SAIDA / f"{t}.csv", dados[t])
    salvar(SAIDA / "participantes.csv", participantes)

    print("\n=== RESUMO POR CASO ===")
    for repo in CONFIG["repos"]:
        ps = [p for p in participantes if p["repo"] == repo]
        prs = [p for p in dados["pull_requests"] if p["repo"] == repo and not p["autor_eh_bot"]]
        integ = [p for p in prs if p["foi_integrado"] == "True"]
        auto = sum(p["auto_integrado_sem_revisao"] for p in integ)
        print(f"{repo}: pessoas={len(ps)} | nucleo(ativ.)={sum(p['nucleo_atividade'] for p in ps)} "
              f"| contrib. unica={sum(p['contribuicao_unica'] for p in ps)} "
              f"| PRs humanos integrados={len(integ)} | auto-integrados s/ revisao={auto}")


if __name__ == "__main__":
    main()
