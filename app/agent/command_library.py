"""Biblioteca hardcoded de comandos de terminal + busca eficiente.

Por que hardcoded e por que uma TOOL de busca (e não no system prompt):
- Despejar o catálogo inteiro no contexto custaria muitos tokens por turno, à
  toa (na maioria das mensagens a Helena não precisa dele). Aqui o custo no
  baseline é só a declaração de `buscar_comando`; o conteúdo só entra quando ela
  chama a tool, e ainda limitado aos poucos comandos relevantes.
- O modelo já conhece os comandos comuns; o valor desta lib é reunir uso, flags
  úteis e COMBINAÇÕES (pipes) num formato compacto e confiável, filtrado pelo SO
  do dispositivo onde a Helena roda.

Busca: pontua por palavra-chave sobre nome + propósito + keywords + categoria e
devolve só os melhores resultados, já formatados de forma enxuta.
"""
from __future__ import annotations

import math
import platform
import unicodedata

from google.genai import types

# --------------------------------------------------------------------------- #
# Catálogo — cada entrada:
#   name    nome (com apelido entre parênteses quando ajuda)
#   cat     categoria (também vira termo de busca)
#   purpose o que faz, em uma linha
#   usage   forma de uso mínima
#   flags   flags mais úteis (curto), ou ""
#   combos  exemplos de combinação/pipe (o ponto forte da lib)
#   kw      sinônimos/termos de busca (PT e EN)
#   os      "all" | "unix" (linux+mac) | "linux" | "win" | "mac"
# --------------------------------------------------------------------------- #
_CATALOG: list[dict] = [
    # ---- navegação / arquivos ----
    {"name": "ls", "cat": "arquivos", "purpose": "lista arquivos e pastas",
     "usage": "ls [caminho]", "flags": "-l detalhe, -a inclui ocultos, -h tamanhos legíveis, -t ordena por data, -R recursivo",
     "combos": ["ls -lah", "ls -lt | head"], "kw": ["listar", "dir", "arquivos", "pastas", "list"], "os": "unix"},
    {"name": "cd", "cat": "arquivos", "purpose": "muda de diretório (no shell). Para a Helena, use a tool mudar_diretorio (persiste).",
     "usage": "cd caminho", "flags": "cd - volta ao anterior, cd ~ vai pro home",
     "combos": ["cd projeto && ls"], "kw": ["diretorio", "pasta", "navegar", "change dir"], "os": "all"},
    {"name": "pwd", "cat": "arquivos", "purpose": "mostra o diretório atual",
     "usage": "pwd", "flags": "", "combos": [], "kw": ["onde estou", "diretorio atual", "current dir"], "os": "unix"},
    {"name": "cp", "cat": "arquivos", "purpose": "copia arquivos/pastas",
     "usage": "cp origem destino", "flags": "-r recursivo (pastas), -i pergunta antes de sobrescrever, -a preserva tudo",
     "combos": ["cp -r src/ backup/"], "kw": ["copiar", "copy", "duplicar"], "os": "unix"},
    {"name": "mv", "cat": "arquivos", "purpose": "move ou renomeia arquivos/pastas",
     "usage": "mv origem destino", "flags": "-i pergunta antes de sobrescrever, -n não sobrescreve",
     "combos": ["mv velho.txt novo.txt"], "kw": ["mover", "renomear", "rename", "move"], "os": "unix"},
    {"name": "rm", "cat": "arquivos", "purpose": "apaga arquivos/pastas (CUIDADO: não tem lixeira)",
     "usage": "rm arquivo", "flags": "-r recursivo, -f força, -i pergunta antes (mais seguro)",
     "combos": ["rm -rf build/  # apaga a pasta build inteira"], "kw": ["apagar", "deletar", "remover", "delete", "remove"], "os": "unix"},
    {"name": "mkdir", "cat": "arquivos", "purpose": "cria pastas",
     "usage": "mkdir nome", "flags": "-p cria os pais que faltam (não dá erro se já existe)",
     "combos": ["mkdir -p a/b/c"], "kw": ["criar pasta", "diretorio", "make dir"], "os": "unix"},
    {"name": "touch", "cat": "arquivos", "purpose": "cria arquivo vazio ou atualiza a data dele",
     "usage": "touch arquivo", "flags": "", "combos": ["touch novo.py"], "kw": ["criar arquivo", "arquivo vazio", "create file"], "os": "unix"},
    {"name": "cat", "cat": "arquivos", "purpose": "imprime o conteúdo de um arquivo",
     "usage": "cat arquivo", "flags": "-n numera as linhas",
     "combos": ["cat a.txt b.txt > junto.txt"], "kw": ["ver arquivo", "conteudo", "ler", "read file"], "os": "unix"},
    {"name": "less", "cat": "arquivos", "purpose": "vê arquivo grande página a página (rolando)",
     "usage": "less arquivo", "flags": "q sai, / busca, G vai ao fim",
     "combos": ["dmesg | less"], "kw": ["paginar", "ver grande", "rolar", "pager"], "os": "unix"},
    {"name": "head / tail", "cat": "arquivos", "purpose": "primeiras / últimas linhas de um arquivo",
     "usage": "head -n 20 arquivo  |  tail -n 20 arquivo", "flags": "-n N nº de linhas, tail -f acompanha em tempo real (logs)",
     "combos": ["tail -f app.log", "sort nums | head -n 5"], "kw": ["primeiras linhas", "ultimas", "log", "acompanhar", "follow"], "os": "unix"},
    {"name": "tree", "cat": "arquivos", "purpose": "mostra a árvore de pastas/arquivos",
     "usage": "tree [caminho]", "flags": "-L N limita a profundidade, -a inclui ocultos, -I 'padrão' ignora",
     "combos": ["tree -L 2 -I 'node_modules|.git'"], "kw": ["arvore", "estrutura", "hierarquia"], "os": "unix"},
    {"name": "ln", "cat": "arquivos", "purpose": "cria link (atalho) para arquivo/pasta",
     "usage": "ln -s alvo link", "flags": "-s link simbólico (o usual)",
     "combos": ["ln -s ~/projeto/.env .env"], "kw": ["link", "atalho", "symlink"], "os": "unix"},
    {"name": "stat", "cat": "arquivos", "purpose": "detalhes de um arquivo (tamanho, datas, permissões)",
     "usage": "stat arquivo", "flags": "", "combos": [], "kw": ["metadados", "tamanho", "info arquivo"], "os": "unix"},

    # ---- busca ----
    {"name": "rg (ripgrep)", "cat": "busca", "purpose": "busca texto recursiva ultrarrápida em arquivos (respeita .gitignore)",
     "usage": "rg 'padrão' [caminho]", "flags": "-i ignora maiúsc., -l só nomes de arquivo, -n nº da linha, -t py só .py, --hidden inclui ocultos, -C 3 mostra contexto",
     "combos": ["rg -l 'TODO' | xargs sed -i 's/TODO/FEITO/g'", "rg -n 'def ' -t py"], "kw": ["procurar texto", "grep", "achar", "search", "buscar codigo", "find text"], "os": "all"},
    {"name": "grep", "cat": "busca", "purpose": "filtra linhas que casam com um padrão (regex)",
     "usage": "grep 'padrão' arquivo", "flags": "-r recursivo, -i ignora maiúsc., -n nº linha, -v inverte, -E regex estendida, -c conta",
     "combos": ["ps aux | grep python", "cat log | grep -i erro"], "kw": ["filtrar", "procurar texto", "regex", "match"], "os": "unix"},
    {"name": "fd", "cat": "busca", "purpose": "acha arquivos por nome, rápido e amigável (respeita .gitignore)",
     "usage": "fd padrão [caminho]", "flags": "-e py por extensão, -t f só arquivos / -t d só pastas, -H inclui ocultos",
     "combos": ["fd -e py | xargs wc -l"], "kw": ["achar arquivo", "encontrar", "find", "por nome", "locate"], "os": "all"},
    {"name": "find", "cat": "busca", "purpose": "acha arquivos por nome/tamanho/data e AGE sobre eles",
     "usage": "find caminho -name '*.py'", "flags": "-name padrão, -type f/d, -mtime -7 (últimos 7 dias), -size +10M, -exec CMD {} \\;",
     "combos": ["find . -name '*.log' -mtime +30 -delete", "find . -type f -exec chmod 644 {} \\;"], "kw": ["procurar arquivo", "busca avançada", "por data", "por tamanho"], "os": "unix"},
    {"name": "which / type", "cat": "busca", "purpose": "mostra o caminho do executável de um comando",
     "usage": "which python", "flags": "type -a mostra todos os matches (bash)",
     "combos": ["which node && node -v"], "kw": ["onde esta o binario", "caminho do programa", "executavel"], "os": "unix"},
    {"name": "locate", "cat": "busca", "purpose": "acha arquivos por nome via índice (bem rápido; precisa updatedb)",
     "usage": "locate nome", "flags": "-i ignora maiúsc.", "combos": ["sudo updatedb && locate helena"], "kw": ["indice", "achar rapido", "banco de arquivos"], "os": "linux"},

    # ---- texto / dados ----
    {"name": "sed", "cat": "texto", "purpose": "edita texto em fluxo (substituir, apagar, extrair linhas)",
     "usage": "sed 's/velho/novo/g' arquivo", "flags": "-i edita o arquivo no lugar, -n + p imprime só o que casa, '5,10p' faixa de linhas",
     "combos": ["sed -i 's/http:/https:/g' config", "sed -n '10,20p' arquivo"], "kw": ["substituir", "replace", "editar texto", "stream editor"], "os": "unix"},
    {"name": "awk", "cat": "texto", "purpose": "processa texto por colunas/campos (relatórios, somas)",
     "usage": "awk '{print $1}' arquivo", "flags": "-F',' separador, NR nº linha, NF nº campos",
     "combos": ["ps aux | awk '{s+=$6} END{print s/1024 \" MB\"}'", "awk -F: '{print $1}' /etc/passwd"], "kw": ["colunas", "campos", "somar", "relatorio", "csv"], "os": "unix"},
    {"name": "jq", "cat": "texto", "purpose": "consulta e transforma JSON no terminal",
     "usage": "jq '.campo' arquivo.json", "flags": ".a.b navega, .[] itera array, -r texto cru (sem aspas), 'map(...)' transforma",
     "combos": ["curl -s api/users | jq '.[].name'", "cat pkg.json | jq -r .version"], "kw": ["json", "parsear", "api", "extrair campo"], "os": "all"},
    {"name": "cut", "cat": "texto", "purpose": "corta colunas de cada linha",
     "usage": "cut -d',' -f1,3 arquivo", "flags": "-d delimitador, -f campos, -c caracteres",
     "combos": ["echo a:b:c | cut -d: -f2"], "kw": ["colunas", "cortar", "campos", "delimitador"], "os": "unix"},
    {"name": "sort / uniq", "cat": "texto", "purpose": "ordena linhas / remove ou conta duplicatas",
     "usage": "sort arquivo | uniq -c", "flags": "sort -n numérico, -r reverso, -k coluna; uniq -c conta, -d só duplicadas",
     "combos": ["cat log | sort | uniq -c | sort -rn | head  # top ocorrências"], "kw": ["ordenar", "duplicados", "contar", "ranking", "dedup"], "os": "unix"},
    {"name": "wc", "cat": "texto", "purpose": "conta linhas, palavras e bytes",
     "usage": "wc -l arquivo", "flags": "-l linhas, -w palavras, -c bytes",
     "combos": ["fd -e py | xargs wc -l | tail -1"], "kw": ["contar linhas", "quantas linhas", "count"], "os": "unix"},
    {"name": "tr", "cat": "texto", "purpose": "troca/remove caracteres (fluxo)",
     "usage": "tr 'a-z' 'A-Z'", "flags": "-d apaga, -s comprime repetidos",
     "combos": ["echo oi | tr a-z A-Z", "tr -d '\\r' < win.txt > unix.txt"], "kw": ["maiuscula", "trocar caractere", "translate"], "os": "unix"},
    {"name": "xargs", "cat": "texto", "purpose": "transforma a saída de um comando em ARGUMENTOS de outro",
     "usage": "... | xargs comando", "flags": "-I{} usa {} como placeholder, -n1 um por vez, -P4 paralelo",
     "combos": ["fd -e tmp | xargs rm", "rg -l TODO | xargs -I{} echo revisar {}"], "kw": ["passar argumentos", "para cada", "pipe para comando", "batch"], "os": "unix"},
    {"name": "tee", "cat": "texto", "purpose": "mostra na tela E salva num arquivo ao mesmo tempo",
     "usage": "... | tee arquivo", "flags": "-a acrescenta em vez de sobrescrever",
     "combos": ["make 2>&1 | tee build.log"], "kw": ["salvar e ver", "gravar saida", "log"], "os": "unix"},
    {"name": "diff", "cat": "texto", "purpose": "compara dois arquivos linha a linha",
     "usage": "diff a b", "flags": "-u formato unificado (patch), -r recursivo em pastas",
     "combos": ["diff -u antigo novo > mudancas.patch"], "kw": ["comparar", "diferenca", "compare"], "os": "unix"},

    # ---- git ----
    {"name": "git status/add/commit", "cat": "git", "purpose": "vê mudanças e faz commit",
     "usage": "git status; git add -A; git commit -m 'msg'", "flags": "add -A tudo, add -p por partes, commit --amend corrige o último",
     "combos": ["git add -A && git commit -m 'fix'"], "kw": ["versionar", "commit", "salvar codigo", "staging"], "os": "all"},
    {"name": "git log/diff", "cat": "git", "purpose": "histórico e diferenças",
     "usage": "git log --oneline; git diff", "flags": "log --oneline -10, log -p mostra patches, diff --staged o que vai no commit",
     "combos": ["git log --oneline -5", "git diff HEAD~1"], "kw": ["historico", "commits", "o que mudou", "blame"], "os": "all"},
    {"name": "git branch/checkout", "cat": "git", "purpose": "cria e troca de branch",
     "usage": "git checkout -b nova", "flags": "switch -c cria e troca, checkout -- arquivo descarta mudança",
     "combos": ["git checkout -b feature/x"], "kw": ["branch", "ramo", "trocar branch", "criar branch"], "os": "all"},
    {"name": "git pull/push", "cat": "git", "purpose": "sincroniza com o remoto",
     "usage": "git pull; git push", "flags": "push -u origin branch (primeira vez), pull --rebase evita merge sujo",
     "combos": ["git push -u origin main"], "kw": ["sincronizar", "enviar", "baixar", "remoto", "sync"], "os": "all"},
    {"name": "git stash", "cat": "git", "purpose": "guarda mudanças não commitadas temporariamente",
     "usage": "git stash; git stash pop", "flags": "list lista, pop restaura, drop descarta",
     "combos": ["git stash && git pull && git stash pop"], "kw": ["guardar mudancas", "temporario", "esconder"], "os": "all"},

    # ---- processos / sistema ----
    {"name": "ps", "cat": "processos", "purpose": "lista processos em execução",
     "usage": "ps aux", "flags": "aux todos com detalhe; combine com grep",
     "combos": ["ps aux | grep python"], "kw": ["processos", "o que esta rodando", "pid"], "os": "unix"},
    {"name": "top / htop", "cat": "processos", "purpose": "monitor de processos ao vivo (CPU/RAM)",
     "usage": "htop", "flags": "htop: F6 ordena, F9 mata, / busca; top: q sai",
     "combos": [], "kw": ["monitor", "cpu", "memoria", "uso", "task manager"], "os": "unix"},
    {"name": "kill / pkill", "cat": "processos", "purpose": "encerra processos",
     "usage": "kill PID  |  pkill nome", "flags": "-9 força (SIGKILL), -15 padrão (pede pra sair); pkill -f casa a linha toda",
     "combos": ["pkill -f 'python run.py'", "kill -9 $(pgrep -f helena)"], "kw": ["matar", "encerrar", "parar processo", "terminar"], "os": "unix"},
    {"name": "lsof", "cat": "processos", "purpose": "vê arquivos/portas abertos por processos",
     "usage": "lsof -i :5000", "flags": "-i :PORTA quem usa a porta, -p PID por processo",
     "combos": ["lsof -i :5000  # quem ocupa a porta 5000"], "kw": ["porta em uso", "quem usa a porta", "arquivos abertos"], "os": "unix"},
    {"name": "systemctl", "cat": "sistema", "purpose": "controla serviços do systemd (start/stop/status)",
     "usage": "systemctl status serviço", "flags": "--user p/ serviços do usuário, enable liga no boot, restart reinicia",
     "combos": ["systemctl --user restart helena", "systemctl status nginx"], "kw": ["servico", "daemon", "iniciar servico", "reiniciar"], "os": "linux"},
    {"name": "journalctl", "cat": "sistema", "purpose": "lê os logs do systemd",
     "usage": "journalctl -u serviço", "flags": "-f acompanha, -e vai ao fim, --user, -n 100 últimas linhas",
     "combos": ["journalctl --user -u helena -f"], "kw": ["logs do sistema", "log servico", "erro servico"], "os": "linux"},
    {"name": "df / du", "cat": "sistema", "purpose": "espaço em disco (df) / tamanho de pastas (du)",
     "usage": "df -h  |  du -sh *", "flags": "-h legível; du -sh * tamanho de cada item; du -sh . total daqui",
     "combos": ["du -sh * | sort -rh | head  # maiores pastas"], "kw": ["espaco em disco", "tamanho pasta", "disco cheio", "storage"], "os": "unix"},
    {"name": "free / uptime", "cat": "sistema", "purpose": "memória livre (free) / carga e tempo ligado (uptime)",
     "usage": "free -h  |  uptime", "flags": "free -h legível",
     "combos": [], "kw": ["memoria livre", "ram", "carga", "load average"], "os": "linux"},
    {"name": "env / export", "cat": "sistema", "purpose": "variáveis de ambiente: ver e definir",
     "usage": "env  |  export VAR=valor", "flags": "printenv VAR mostra uma; export torna visível a subprocessos",
     "combos": ["export DEBUG=1 && python run.py"], "kw": ["variavel de ambiente", "env var", "configuracao"], "os": "unix"},
    {"name": "chmod / chown", "cat": "sistema", "purpose": "muda permissões (chmod) / dono (chown)",
     "usage": "chmod +x script.sh  |  chown user:grp arquivo", "flags": "chmod 755/644 comum, -R recursivo; chmod +x torna executável",
     "combos": ["chmod +x helena", "sudo chown -R $USER:$USER pasta"], "kw": ["permissao", "executavel", "dono", "acesso", "negado"], "os": "unix"},

    # ---- rede ----
    {"name": "curl", "cat": "rede", "purpose": "faz requisições HTTP (baixar, testar API)",
     "usage": "curl url", "flags": "-s silencioso, -o arquivo salva, -L segue redirect, -H header, -d dados (POST), -X método, -i mostra headers",
     "combos": ["curl -s api/x | jq .", "curl -X POST -d '{\"a\":1}' -H 'Content-Type: application/json' url"], "kw": ["http", "api", "baixar", "requisicao", "testar endpoint", "download"], "os": "all"},
    {"name": "wget", "cat": "rede", "purpose": "baixa arquivos da web",
     "usage": "wget url", "flags": "-O nome salva com outro nome, -c continua download, -r recursivo",
     "combos": ["wget -c https://.../grande.iso"], "kw": ["baixar", "download", "arquivo web"], "os": "linux"},
    {"name": "ss / netstat", "cat": "rede", "purpose": "mostra portas e conexões de rede",
     "usage": "ss -tulpn", "flags": "-t tcp, -u udp, -l escutando, -p processo, -n numérico",
     "combos": ["ss -tulpn | grep 5000"], "kw": ["portas abertas", "conexoes", "quem escuta", "rede"], "os": "linux"},
    {"name": "ping", "cat": "rede", "purpose": "testa se um host responde",
     "usage": "ping host", "flags": "-c 4 manda 4 e para",
     "combos": ["ping -c 3 google.com"], "kw": ["testar conexao", "host responde", "internet"], "os": "all"},
    {"name": "ssh / scp", "cat": "rede", "purpose": "acessa outra máquina (ssh) / copia arquivos por rede (scp)",
     "usage": "ssh user@host  |  scp arquivo user@host:/dest", "flags": "ssh -p porta, scp -r recursivo",
     "combos": ["scp -r site/ user@server:/var/www/"], "kw": ["remoto", "servidor", "copiar por rede", "acesso remoto"], "os": "unix"},

    # ---- compactação ----
    {"name": "tar", "cat": "arquivos", "purpose": "empacota/extrai arquivos (.tar, .tar.gz)",
     "usage": "tar -czf saida.tar.gz pasta/  |  tar -xzf arquivo.tar.gz", "flags": "c cria, x extrai, z gzip, f arquivo, v verboso, -C destino",
     "combos": ["tar -czf backup.tar.gz projeto/", "tar -xzf pacote.tar.gz -C /destino"], "kw": ["compactar", "extrair", "backup", "targz", "descompactar", "zip"], "os": "unix"},
    {"name": "zip / unzip", "cat": "arquivos", "purpose": "cria/extrai .zip",
     "usage": "zip -r saida.zip pasta/  |  unzip arquivo.zip", "flags": "zip -r recursivo; unzip -d destino",
     "combos": ["unzip -d /destino pacote.zip"], "kw": ["zip", "compactar", "extrair", "descompactar"], "os": "all"},

    # ---- dev / linguagens ----
    {"name": "uv", "cat": "dev", "purpose": "gerenciador de pacotes/venv Python rápido (usado neste projeto)",
     "usage": "uv sync; uv run script.py", "flags": "sync instala do lock, add pacote, run roda no venv, pip compat",
     "combos": ["uv add requests && uv run run.py"], "kw": ["python", "dependencias", "venv", "pip", "instalar pacote"], "os": "all"},
    {"name": "python", "cat": "dev", "purpose": "roda scripts e um-liners Python",
     "usage": "python arquivo.py", "flags": "-m módulo (ex.: -m http.server), -c 'código', -i interativo depois",
     "combos": ["python -m http.server 8000", "python -c 'import sys; print(sys.version)'"], "kw": ["python", "rodar script", "executar py"], "os": "all"},
    {"name": "pip", "cat": "dev", "purpose": "instala pacotes Python (prefira uv neste projeto)",
     "usage": "pip install pacote", "flags": "-r requirements.txt, -U atualiza, list lista, show detalha",
     "combos": ["pip install -r requirements.txt"], "kw": ["instalar pacote", "python", "dependencia"], "os": "all"},
    {"name": "npm / npx", "cat": "dev", "purpose": "gerencia pacotes Node (npm) / roda binário sem instalar (npx)",
     "usage": "npm install; npm run script; npx ferramenta", "flags": "install -g global, run <script> do package.json, ci instala do lock",
     "combos": ["npm ci && npm run build"], "kw": ["node", "javascript", "instalar", "frontend", "yarn"], "os": "all"},
    {"name": "make", "cat": "dev", "purpose": "roda tarefas definidas num Makefile",
     "usage": "make alvo", "flags": "-j paralelo, -n só mostra o que faria",
     "combos": ["make -j4"], "kw": ["build", "compilar", "makefile", "tarefa"], "os": "unix"},
    {"name": "docker", "cat": "dev", "purpose": "containers: rodar, listar, logs, imagens",
     "usage": "docker ps; docker run img; docker logs cont", "flags": "ps -a inclui parados, run -d fundo/-p porta, exec -it entra, compose up",
     "combos": ["docker compose up -d", "docker exec -it app bash"], "kw": ["container", "imagem", "docker compose", "conteiner"], "os": "all"},

    # ---- utilidades ----
    {"name": "echo / printf", "cat": "texto", "purpose": "imprime texto (echo) / com formatação (printf)",
     "usage": "echo texto  |  printf '%s\\n' x", "flags": "echo -n sem quebra de linha, -e interpreta \\n",
     "combos": ["echo 'export X=1' >> ~/.bashrc"], "kw": ["imprimir", "escrever", "print", "mostrar"], "os": "unix"},
    {"name": "watch", "cat": "sistema", "purpose": "repete um comando a cada N segundos (monitorar)",
     "usage": "watch -n 2 comando", "flags": "-n intervalo em s, -d destaca o que mudou",
     "combos": ["watch -n 1 'ls -l | wc -l'"], "kw": ["repetir", "monitorar", "acompanhar", "loop"], "os": "linux"},
    {"name": "history", "cat": "sistema", "purpose": "mostra os últimos comandos digitados",
     "usage": "history", "flags": "combine com grep; !N re-executa o comando N",
     "combos": ["history | grep git"], "kw": ["comandos anteriores", "o que digitei", "historico shell"], "os": "unix"},
    {"name": "alias", "cat": "sistema", "purpose": "cria um atalho para um comando",
     "usage": "alias nome='comando'", "flags": "sem args lista todos; ponha no ~/.bashrc pra persistir",
     "combos": ["alias gs='git status'"], "kw": ["atalho", "encurtar comando", "apelido"], "os": "unix"},
    {"name": "sudo", "cat": "sistema", "purpose": "roda um comando como administrador (root)",
     "usage": "sudo comando", "flags": "-u user como outro usuário; use só quando necessário",
     "combos": ["sudo systemctl restart nginx"], "kw": ["administrador", "root", "permissao", "superusuario"], "os": "unix"},

    # ---- windows (quando o SO for Windows) ----
    {"name": "dir", "cat": "arquivos", "purpose": "lista arquivos e pastas (Windows)",
     "usage": "dir [caminho]", "flags": "/a inclui ocultos, /s recursivo, /o:d ordena por data",
     "combos": [], "kw": ["listar", "ls", "arquivos"], "os": "win"},
    {"name": "Get-ChildItem (ls)", "cat": "arquivos", "purpose": "lista itens no PowerShell",
     "usage": "Get-ChildItem [caminho]", "flags": "-Recurse recursivo, -Force inclui ocultos, -Filter *.py",
     "combos": ["Get-ChildItem -Recurse -Filter *.py"], "kw": ["listar", "ls", "powershell", "arquivos"], "os": "win"},
    {"name": "Select-String", "cat": "busca", "purpose": "busca texto em arquivos (grep do PowerShell)",
     "usage": "Select-String 'padrão' arquivo", "flags": "-Pattern regex, -Path caminho, -List só nomes",
     "combos": ["Get-ChildItem -Recurse *.py | Select-String 'TODO'"], "kw": ["grep", "procurar texto", "powershell"], "os": "win"},
    {"name": "taskkill / tasklist", "cat": "processos", "purpose": "lista (tasklist) e encerra (taskkill) processos no Windows",
     "usage": "tasklist  |  taskkill /PID 123 /F", "flags": "/IM nome.exe por nome, /F força, /T inclui filhos",
     "combos": ["taskkill /IM python.exe /F"], "kw": ["matar processo", "encerrar", "kill", "windows"], "os": "win"},
]


# --------------------------------------------------------------------------- #
# SO do dispositivo → filtro de relevância
# --------------------------------------------------------------------------- #

def _device_os() -> str:
    """'win' | 'mac' | 'linux' conforme a máquina onde a Helena roda."""
    s = platform.system()
    if s == "Windows":
        return "win"
    if s == "Darwin":
        return "mac"
    return "linux"


def _os_matches(entry_os: str, dev: str) -> bool:
    if entry_os == "all":
        return True
    if entry_os == "unix":
        return dev in ("linux", "mac")
    return entry_os == dev


def _norm(text: str) -> str:
    """minúsculo e sem acento — busca robusta a 'diretorio' vs 'diretório'."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# conectivos (PT/EN) que só geram ruído — e casam como substring dentro de nomes
# de comando (ex.: "em" dentro de "systemctl"). Removidos antes de pontuar.
_STOPWORDS = {
    "em", "de", "do", "da", "dos", "das", "no", "na", "nos", "nas", "um", "uma",
    "uns", "umas", "os", "as", "ao", "aos", "e", "ou", "para", "pra", "por",
    "com", "sem", "que", "qual", "quais", "meu", "minha", "como", "num", "numa",
    "in", "on", "of", "to", "the", "my", "me", "for", "and", "or", "is", "how",
}


def _terms(query: str) -> list[str]:
    return [
        t for t in _norm(query).split()
        if len(t) > 1 and t not in _STOPWORDS
    ]


# --------------------------------------------------------------------------- #
# Busca
# --------------------------------------------------------------------------- #

def _fields(entry: dict) -> tuple[str, str, str, str]:
    """Campos normalizados (name, kw, cat, purpose) de uma entrada."""
    return (
        _norm(entry["name"]),
        _norm(" ".join(entry["kw"])),
        _norm(entry["cat"]),
        _norm(entry["purpose"]),
    )


def search(query: str, limit: int = 6) -> list[dict]:
    """Pontua o catálogo (filtrado pelo SO) contra os termos da consulta e
    devolve as melhores entradas. Cada termo é ponderado por raridade (IDF):
    palavra específica ('compactar') pesa mais que genérica ('arquivo')."""
    dev = _device_os()
    terms = _terms(query)
    if not terms:
        return []

    pool = [e for e in _CATALOG if _os_matches(e["os"], dev)]
    fields = {id(e): _fields(e) for e in pool}
    n = len(pool)

    # IDF: em quantas entradas o termo aparece (qualquer campo) → peso.
    idf: dict[str, float] = {}
    for t in terms:
        df = sum(1 for e in pool if any(t in f for f in fields[id(e)]))
        idf[t] = math.log((n + 1) / (df + 1)) + 0.3  # sempre positivo

    scored: list[tuple[float, dict]] = []
    for e in pool:
        name, kw, cat, purpose = fields[id(e)]
        score = 0.0
        for t in terms:
            w = idf[t]
            if t in name:
                score += 5 * w
            if t in kw:
                score += 3 * w
            if t in cat:
                score += 2 * w
            if t in purpose:
                score += 1 * w
        if score:
            scored.append((score, e))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:limit]]


def _format(entry: dict) -> str:
    lines = [f"• {entry['name']} — {entry['purpose']}", f"  uso: {entry['usage']}"]
    if entry.get("flags"):
        lines.append(f"  flags: {entry['flags']}")
    for combo in entry.get("combos", []):
        lines.append(f"  combinar: {combo}")
    return "\n".join(lines)


def _categories(dev: str) -> list[str]:
    cats = []
    for e in _CATALOG:
        if _os_matches(e["os"], dev) and e["cat"] not in cats:
            cats.append(e["cat"])
    return cats


# --------------------------------------------------------------------------- #
# Tool
# --------------------------------------------------------------------------- #

BUSCAR_COMANDO_DECL = types.FunctionDeclaration(
    name="buscar_comando",
    description=(
        "Consulta a biblioteca interna de comandos de terminal quando você tiver "
        "dúvida de qual usar, quais flags ou como COMBINAR (pipes) — antes de "
        "montar um executar_shell. Passe uma consulta por objetivo/palavra-chave "
        "(ex.: 'achar texto em arquivos', 'ver porta em uso', 'compactar pasta'). "
        "Devolve poucos comandos relevantes com uso, flags e exemplos de "
        "combinação, já filtrados pelo sistema operacional do dispositivo."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "consulta": types.Schema(
                type=types.Type.STRING,
                description="O que você quer fazer, em palavras-chave ou objetivo.",
            ),
        },
        required=["consulta"],
    ),
)


def buscar_comando(user_id: int, args: dict) -> dict:
    """Handler: busca no catálogo e devolve texto compacto p/ o modelo."""
    consulta = (args.get("consulta") or args.get("query") or "").strip()
    if not consulta:
        return {"ok": False, "error": "consulta vazia"}
    hits = search(consulta)
    if not hits:
        cats = ", ".join(_categories(_device_os()))
        return {
            "ok": True,
            "encontrados": 0,
            "resultado": (
                "Nenhum comando casou com essa consulta. Tente termos de outra "
                f"forma. Categorias disponíveis: {cats}."
            ),
        }
    texto = "\n".join(_format(h) for h in hits)
    return {"ok": True, "encontrados": len(hits), "resultado": texto}
