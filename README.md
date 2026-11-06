# claude-code-openai-proxy

Petit proxy HTTP local qui expose une API **compatible OpenAI**
(`/v1/chat/completions`) et répond en appelant **Claude Code** en mode non
interactif (`claude -p`). Il permet de brancher ponctuellement une application
qui parle « OpenAI » (par exemple un fournisseur `openai_compatible`) sur Claude,
avec l'abonnement Claude Code déjà connecté sur la machine, sans clé d'API.

Un seul fichier Python, sans dépendance (bibliothèque standard uniquement).

> Outil d'essai et de dépannage, pas un service de production : un appel =
> un processus `claude -p`, pas de vrai streaming, pas d'outils. Pour un usage
> régulier ou partagé, utilisez l'API Anthropic. Vérifiez que votre usage reste
> conforme aux conditions de votre abonnement.

## Prérequis

- Python 3.9 ou plus ;
- [Claude Code](https://docs.claude.com/en/docs/claude-code) installé et
  connecté (`claude` dans le `PATH`, `claude -p "bonjour"` doit répondre).

## Lancement

```sh
python3 -I proxy.py            # écoute sur 127.0.0.1:18030
python3 -I proxy.py 18031      # autre port
PROXY_MODEL=claude-sonnet-5-5 PROXY_LOG=proxy.log python3 -I proxy.py
```

| Variable        | Défaut            | Rôle                                         |
|-----------------|-------------------|----------------------------------------------|
| `PROXY_MODEL`   | `claude-opus-5-5` | modèle passé à `claude --model`              |
| `PROXY_HOST`    | `127.0.0.1`       | adresse d'écoute (gardez la boucle locale)   |
| `PROXY_PORT`    | `18030`           | port (l'argument en ligne de commande prime) |
| `PROXY_LOG`     | *(stderr)*        | fichier de journal                           |
| `PROXY_TIMEOUT` | `1800`            | délai maximal d'un appel, en secondes        |

Côté client, configurez l'URL de base `http://127.0.0.1:18030/v1`, le modèle
servi (`claude-opus-5-5` par défaut) et une clé d'API quelconque si le client
en exige une (elle est ignorée).

```sh
curl -s http://127.0.0.1:18030/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-5-5","messages":[{"role":"user","content":"Dis bonjour."}]}'
```

## Ce qui est pris en charge

- `GET /v1/models` : renvoie le modèle servi ;
- `POST /v1/chat/completions` :
  - `messages` : les messages `system` deviennent le prompt système
    (`--system-prompt`) ; une question unique passe telle quelle, une
    conversation à plusieurs tours est transmise sous forme de texte balisé ;
  - `response_format: {"type": "json_schema", ...}` : sortie structurée via
    `claude --json-schema` (le JSON validé est renvoyé dans `content`) ;
  - `stream: true` : réponse SSE au format OpenAI, en **un seul morceau** une
    fois la génération terminée, suivi de l'usage puis `[DONE]` ;
  - `usage` : jetons d'entrée (cache compris) et de sortie rapportés par Claude
    Code. Le coût de chaque appel est écrit dans le journal.

Ne sont pas pris en charge : `tools` / appels de fonctions (requête refusée en
400), images, `temperature`, `max_tokens` et les autres paramètres
d'échantillonnage (ignorés), le choix du modèle par requête (le champ `model`
est ignoré, c'est `PROXY_MODEL` qui compte).

## Isolation

Chaque appel lance :

```sh
claude -p --model "$PROXY_MODEL" --tools "" --strict-mcp-config \
  --setting-sources "" --output-format json [--system-prompt …] [--json-schema …]
```

dans un répertoire temporaire vide. Claude n'a donc **aucun outil** (ni
lecture de fichiers, ni shell, ni web), aucun serveur MCP, et ne charge ni
vos réglages ni le `CLAUDE.md` d'un projet : il ne voit que ce que le client
envoie. Le proxy écoute par défaut sur la boucle locale et n'a pas
d'authentification ; ne l'exposez pas sur le réseau.

## Licence

[MIT](LICENSE)
