# BR-voc

Consulta à grafia das palavras no **Vocabulário Ortográfico da Língua Portuguesa (VOLP)**, da Academia Brasileira de Letras, direto da área de trabalho.

<img width="593" height="726" alt="satty-20261119-141123" src="https://github.com/user-attachments/assets/8944075e-e23d-4bcf-bf43-5b863e2b2916" />
<img width="590" height="725" alt="satty-20261119-141203" src="https://github.com/user-attachments/assets/72581c58-1028-4edf-af15-f1f4dd78b96d" />


Digite uma palavra e o BR-voc diz se ela está registrada, se existe com outra acentuação ou se não consta, e sugere grafias parecidas quando há erro de digitação. Feito em Python com GTK 4 e libadwaita, para Linux.

> O BR-voc não tem vínculo com a ABL. Ele usa a mesma busca pública que o site da Academia oferece.

## Como funciona

O app tem dois modos de consulta: **online**, que usa o VOLP de verdade, e **offline**, que usa uma lista de palavras livre baixada para o computador.

### Modo online (VOLP)

É o modo padrão. Cada busca é enviada à [busca no vocabulário](https://www.academia.org.br/nossa-lingua/busca-no-vocabulario) do site da ABL, e o resultado mostra:

- **Veredito**: registrada no VOLP, registrada com outra grafia (acento ou maiúscula diferente, por exemplo "cafe" → "café") ou não consta.
- **Classificação gramatical** do verbete, como aparece no VOLP (*s.m.*, *adj.*, *v.*…), com a legenda das abreviaturas ao passar o mouse.
- **Estrangeirismos e topônimos**, marcados como tal.
- **Verbetes que começam com o termo** buscado (o site devolve no máximo 100).
- **Sugestões** ("Você quis dizer?") quando a palavra não é encontrada.

As respostas ficam guardadas num cache local por 30 dias, então repetir uma busca é instantâneo. Se a internet cair, o BR-voc mostra o resultado salvo, mesmo vencido, e avisa a data.

### Modo offline (lista VERO)

A ABL não disponibiliza o VOLP para download. Para funcionar sem internet, o BR-voc pode baixar a lista de palavras pt-BR do **VERO**, o verificador ortográfico usado pelo LibreOffice, que é livre (LGPLv3 / MPL) e segue o Acordo Ortográfico. São cerca de 5,5 MB para baixar e uns 25 MB no disco.

Para ativar, abra **Menu → Uso offline…** e clique em **Baixar**. Depois disso, a lista é usada de duas formas:

- **Automaticamente**, quando o site da ABL não responde (sem conexão, site fora do ar ou bloqueio anti-robô).
- **Sempre**, se você ligar a opção **Usar só a lista offline**. Nesse caso o app não consulta o site da ABL nem com internet.

Um aviso no topo da janela indica sempre que o resultado veio da lista offline.

### O que muda entre os dois modos

A lista offline **não é o VOLP**. Na maioria das palavras as duas concordam, mas em alguns casos elas podem divergir.

| | Online (VOLP) | Offline (VERO) |
|---|---|---|
| Fonte | VOLP, da Academia Brasileira de Letras | Lista do corretor do LibreOffice |
| Precisa de internet | Sim (exceto palavras já em cache) | Não |
| Valor de referência | Grafia oficial | Boa aproximação, mas pode divergir do VOLP |
| Classificação gramatical | Sim | Não |
| Estrangeirismos e topônimos | Identificados | Não identificados |
| Plurais, femininos e conjugações | Em geral só aparecem as formas de dicionário | Reconhece as formas flexionadas (mais de 10 milhões) |
| Verbetes que começam com o termo | Até 100, limite do site | Até 100 |
| Sugestões de grafia | Sim | Sim |

Na dúvida, prefira o resultado online: é ele que vale como referência oficial.

## Instalação

### Arch Linux e derivados

Baixe o pacote `br-voc-<versão>-any.pkg.tar.zst` na página de [releases](https://github.com/narayanls/BR-voc/releases/latest) e instale com:

```sh
sudo pacman -U br-voc-*.pkg.tar.zst
```

### Rodando pelo código-fonte

Em qualquer distribuição com as dependências acima:

```sh
python3 usr/share/br-voc/main.py
```

## Atualizações

O BR-voc procura uma versão nova alguns segundos depois de abrir e também quando você escolhe **Menu → Procurar atualizações**. Se houver, ele mostra as novidades e oferece **Atualizar**: baixa o pacote do último release no GitHub, confere o tamanho e a soma SHA-256 e instala com `pkexec pacman -U`, que pede a sua senha numa janela do sistema. No fim, é só reiniciar o app.

A instalação automática só funciona quando o BR-voc foi instalado pelo pacote (em `/usr/share/br-voc`) num sistema com `pacman` e `pkexec`. Nos outros casos, o app avisa que há uma versão nova e leva você à página do release.

## Atalhos do teclado

| Atalho | Ação |
|---|---|
| `Ctrl+F` ou `Ctrl+L` | Ir para a busca |
| `Enter` | Buscar agora |
| `Esc` | Limpar e voltar ao início |
| `Ctrl+Q` | Sair |

## Onde ficam os dados

| Arquivo | Conteúdo |
|---|---|
| `~/.cache/br-voc/volp.sqlite3` | Cache das respostas do VOLP (apague em **Menu → Limpar cache**) |
| `~/.local/share/br-voc/historico.json` | Últimas 20 buscas |
| `~/.local/share/br-voc/preferencias.json` | Preferências |
| `~/.local/share/br-voc/offline/` | Lista offline, com os créditos e a licença do VERO |

Os caminhos seguem as variáveis `XDG_CACHE_HOME` e `XDG_DATA_HOME`, se estiverem definidas.

## Licença e créditos

O BR-voc é software livre, distribuído sob a [GPL-3.0](LICENSE).

- Os verbetes do modo online vêm da busca pública do [Vocabulário Ortográfico da Língua Portuguesa](https://www.academia.org.br/nossa-lingua/busca-no-vocabulario), mantida pela Academia Brasileira de Letras.
- A lista do modo offline é a do [VERO](https://github.com/LibreOffice/dictionaries/tree/master/pt_BR), verificador ortográfico pt-BR do LibreOffice, distribuída sob LGPLv3 / MPL.
