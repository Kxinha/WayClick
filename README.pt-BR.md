# WayClick

**Um autoclicker que funciona de verdade no Wayland.**

[English](README.md) · [Instalação](#instalação) · [Como usar](#como-usar) · [Como funciona](#como-funciona)

Em vez de brigar com o compositor, o WayClick cria um **mouse virtual no
kernel** através do `/dev/uinput`. Os cliques dele chegam pelo mesmo caminho dos
cliques do seu mouse de verdade — sem X11, sem `xdotool`, sem extensão de
compositor e sem nada rodando como root.

<p align="center">
  <img src="docs/wayclick-ptbr.png" width="46%" alt="WayClick, tema escuro">
  <img src="docs/wayclick-light.png" width="46%" alt="WayClick, tema claro">
</p>

- **De 1 clique/s até 0,1 ms de intervalo** — 10.000 cliques/s, medidos com 100%
  de entrega na janela que recebe
- **Gatilho** em qualquer tecla ou botão do mouse, definido apertando:
  segure o clique esquerdo e ele aperta E, ou um botão do polegar, ou uma tecla
- **Atalho de armar/desarmar** que funciona mesmo com a janela fora de foco
- **Modo segurar** — só clica enquanto você segura o botão do mouse
- **Macro de teclado** — até oito teclas ao mesmo tempo, cada uma capturada
  como se define um atalho e com o seu próprio intervalo; repetindo ou segurando
- **Macros com atalho, estilo AutoHotkey**: cada macro com o seu atalho (tecla
  ou botão do mouse, com modificadores), liga e desliga, enquanto segura, uma
  vez ou sempre, uma lista de passos, e o atalho escondido do jogo como o
  AutoHotkey faz. Um **atalho de Suspend** desliga e liga todos eles, como o
  `F7::Suspend`. **Importar AHK** transforma um script `.ahk` em macros
- **Passos de tela** nesses macros, como o `ImageSearch`, o `PixelSearch` e o
  `PixelGetColor` do AutoHotkey. Ir a um ponto exato e clicar, achar uma imagem, conferir a
  cor de um pixel, esperar até aparecer ou sumir e escolher o que vem depois,
  postar num webhook do Discord com print da tela, abrir um link. **Escolher
  na tela** pega o ponto, a cor ou a imagem da sua própria tela
- **Passos de farm**: *Vigiar* fica olhando a tela ou uma janela enquanto os
  outros passos rodam, *Janela* acha o jogo e traz para a frente, *Contar* e
  *Parar após N rodadas* terminam um farm sozinhos. **Gravar** transforma o
  que você joga em passos, o **Assistente de farm** monta o laço de lobby,
  partida, fim e de novo de um jogo de tower defense, e **Exportar /
  Importar** compartilha um macro com as imagens num arquivo `.wayclick` só
- **A Doca** abre o jogo num KWin só dela, numa janela do WayClick, e os
  macros marcados com *Jogar na Doca* apertam teclas, clicam e olham a tela
  só lá dentro, então seu mouse e seu teclado ficam livres e a Doca pode
  ficar atrás de outras janelas ou minimizada
- **A aba Tower Defense** mostra o jogo da Doca dentro do WayClick e farma
  um jogo de tower defense de lá, com cinco torres e os lugares delas, uma
  linha do tempo que você grava jogando, o lobby, imagens do fim da partida,
  Iniciar F4, Parar F5 e Pausar F6, um registro e as contagens. Ele só olha
  a tela e aperta teclas, nunca lê a memória do jogo
- **Contas do Sober** abrem mais cópias do Sober ao lado da de sempre, cada
  uma com login do Roblox, configurações e rede próprios, então uma segunda
  conta joga ao mesmo tempo que a primeira, na área de trabalho ou na Doca
- **Mandar tecla para uma janela** — escolha uma janela aberta e alimente ela
  com uma tecla; em alvo X11 chega sem mexer no foco, mesmo minimizada
- **Anti-AFK** — mexe o ponteiro e devolve, com deriva zero
- **Aviso de atualização** — avisa que saiu release nova, do GitHub ou do
  GitLab, sem você ir olhar
- **Ícone na bandeja** em quatro formatos e dez cores, pulsando enquanto
  trabalha e piscando ao ligar; iniciar com o sistema, os esquemas de cor do
  seu sistema, inglês e português

---

## Requisitos

| | |
|---|---|
| Ambiente | KDE Plasma no Wayland |
| Python | 3.9 ou mais novo |
| Bindings Qt | PySide6 (`QtMultimedia` é opcional — só para os bipes) |
| Permissões | seu usuário no grupo `input`, e `/dev/uinput` gravável por ele |
| Passos de tela (opcional) | numpy, PyGObject e GStreamer com o plugin do PipeWire, mais o portal do desktop (`xdg-desktop-portal-kde` no Plasma) |
| Contas do Sober (opcional) | Sober do Flathub, `pasta` do pacote `passt`, util-linux 2.38 ou mais novo, Linux 5.6 ou mais novo, e namespaces de usuário ligados para usuários comuns |

## Instalação

### Pacotes

Pegue um na [release mais recente](https://github.com/gabrielmf1998/WayClick/releases/latest):

| | |
|---|---|
| **Fedora, RHEL** | `sudo dnf install ./wayclick-*.noarch.rpm` |
| **Debian, Ubuntu** | `sudo apt install ./wayclick_*_all.deb` |
| **Arch, Manjaro** | `sudo pacman -U ./wayclick-*-any.pkg.tar.zst` |
| **Qualquer outra** | `chmod +x WayClick-*.AppImage && ./WayClick-*.AppImage` |

Os pacotes já trazem a regra de udev, então só falta entrar uma vez no grupo
`input`: `sudo usermod -aG input $USER`, e sair e entrar de novo. O AppImage
carrega o próprio Python e Qt e não precisa de dependência nenhuma — só os
bipes do atalho ficam de fora, porque o PySide6-Essentials não inclui o
QtMultimedia; ele cai no `paplay` do seu sistema.

Os passos que olham a tela (achar imagem, conferir a cor de um pixel, print
no webhook) usam numpy, PyGObject e GStreamer do seu sistema, que o Python
próprio do AppImage não consegue carregar. Lá eles avisam o que falta e seguem
pelo caminho *Se não*, e as posições são digitadas em vez de escolhidas na
tela. O rpm e o deb recomendam esses pacotes, e o pacote do Arch lista como
opcionais.

### Um comando

```bash
curl -fsSL https://raw.githubusercontent.com/gabrielmf1998/WayClick/main/install.sh | bash
```

Ele detecta sua distro, instala o PySide6, prepara o módulo `uinput` e a regra
de udev, adiciona você ao grupo `input` e coloca o app em `~/.local` com entrada
no menu. Tudo que precisa de `sudo` é anunciado antes de rodar. Depois é só
executar `wayclick` ou procurar **WayClick** no menu de aplicativos.

> Jogar um script direto no `bash` é um voto de confiança. Leia antes se
> preferir: [`install.sh`](install.sh).

### Na mão

São os mesmos quatro passos em qualquer distro; só muda o nome do pacote.

**1. Instale o PySide6**

```bash
sudo pacman -S pyside6                                   # Arch, Manjaro
sudo dnf install python3-pyside6                         # Fedora
sudo apt install python3-pyside6.qtwidgets \
                 python3-pyside6.qtmultimedia            # Debian, Ubuntu
pip install --user PySide6                               # qualquer outra
```

**2. Garanta o módulo `uinput` carregado, agora e no boot**

```bash
sudo modprobe uinput
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
```

**3. Deixe o grupo `input` usar o `/dev/uinput`**

Em várias distros o device nasce como `root:root`, então estar no grupo não
basta:

```bash
sudo tee /etc/udev/rules.d/99-wayclick-uinput.rules <<'EOF'
KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=uinput
```

**4. Entre no grupo `input`**

```bash
sudo usermod -aG input $USER
```

Mudança de grupo só vale para sessões **novas**. Saia e entre de novo — ou abra
assim mesmo: o WayClick percebe a diferença e se reexecuta via `sg input`, que
funciona na hora e não pede senha.

**Rodando**

```bash
git clone https://github.com/gabrielmf1998/WayClick.git
cd WayClick
python3 wayclick.py
```

## Como usar

```bash
wayclick            # normal
wayclick --tray     # começa escondido na bandeja
wayclick --import script.ahk --dry-run   # no que um script do AutoHotkey vira
wayclick --version
```

As páginas ficam numa barra lateral à esquerda, em dois grupos, como nas
Configurações do Sistema. **Automação** tem **Clique**, **Teclado**,
**Atalhos**, **Janela** e **Acionamento**, e **Jogos** tem **Doca**, **Tower
Defense** e **Contas do Sober**. **Atalhos** guarda os macros estilo
AutoHotkey, que rodam nos próprios atalhos e não precisam do Iniciar.
**Clique**, **Teclado** e **Janela** são as três coisas que o Iniciar
executa. Cada uma tem sua caixa de habilitar, e o nome dela na barra ganha um
ponto quando está ligada, então dá para ver o que está armado sem abrir.
Habilite qualquer combinação. **Acionamento** é como tudo liga e desliga, mais
o Anti-AFK, que roda por fora do Iniciar/Parar. **Doca** abre o jogo na
[Doca](#a-doca), e **Contas do Sober** abre
[um segundo Sober](#contas-do-sober).

A busca em cima das páginas (Ctrl+F) acha uma página pelo nome ou por
qualquer texto dela, e Ctrl+Tab ou Ctrl+PgDown vai para a próxima página
(Ctrl+Shift+Tab ou Ctrl+PgUp volta). Numa janela estreita demais para os
nomes a barra mostra só os ícones, com o nome no tooltip e o ponto no ícone,
e a busca vira um botão. O status e o botão Iniciar ficam sempre visíveis
abaixo das páginas, e a janela abre com 709×548 em português (660 de largura
em inglês), o que cabe numa tela de 1366×768.

O resto está na barra de menus: **Arquivo** inicia, para, esconde na bandeja e
sai; **Configurações** tem **Tema** e **Idioma** (English, Português BR), além
dos toggles de som e de iniciar com o sistema; **Ajuda** leva ao projeto e
mostra a versão. As duas preferências ficam salvas. O idioma vem do locale do
sistema, então uma máquina brasileira já abre em português sem configurar nada.

O **Ícone da bandeja** tem submenu próprio: o **formato** (cursor, mouse, ponto
ou anel) e a **cor** — uma das nove fixas, incluindo branco e preto, ou
*Conforme o estado*, que mantém o padrão cinza parado, azul armado, verde
clicando. O contorno alterna entre escuro e claro conforme o preenchimento,
então branco e preto continuam visíveis em qualquer painel. O glifo pulsa
enquanto roda, e ligar ou desligar faz ele saltar de pequeno para o tamanho
cheio atrás de um anel que cresce e some — o retorno que faltava quando se
aciona pelo atalho com a janela escondida.

O **Tema** lê os esquemas de cor instalados no seu sistema
(`/usr/share/color-schemes/*.colors`), então o "Breeze Dark" aqui é o Breeze
Dark de verdade, não uma aproximação — qualquer esquema que você instalar
aparece no menu. Dois esquemas embutidos cobrem quem não tem nenhum, e o
*Sistema* devolve a aparência para o seu ambiente.

### Clique

**Mouse** — escolha qual mouse usar. Todos os que o sistema expõe aparecem na
lista, e o `↻` reprocura depois de plugar um. Isso importa mais do que parece: o
device virtual é criado como um *clone* do mouse escolhido (mesmo nome, vendor e
product id), e é assim que o KDE aplica **a sua** velocidade e perfil de
aceleração nele. Sem clonar, o ponteiro cai no padrão do sistema e a
sensibilidade fica estranha assim que o modo segurar assume.

**Intervalo** — tempo entre cliques, de 1000 ms até 0,1 ms, com o equivalente em
cliques/s logo abaixo. Menor é mais rápido.

**Botão** — esquerdo, direito ou meio.

### Macro de teclado

Aperta uma tecla por um segundo device virtual, um teclado de verdade do ponto
de vista do kernel. Para escolher a **tecla** você clica no campo e aperta a
tecla que quiser, do jeito que se define um atalho — sem lista para caçar, e
qualquer tecla do teclado serve. Depois escolha a **ação**: `Repetir` bate no
intervalo definido, `Segurar` aperta uma vez e mantém pressionada. A tecla é
sempre solta ao parar, ao sair e mesmo se o processo morrer — nunca fica presa.

**+ Adicionar tecla** dá mais uma linha, até oito. Cada linha tem tecla,
intervalo e ação próprios, e roda na sua própria thread — que é o ponto: andar
com `W` a cada segundo enquanto `Espaço` sai a cada 50 ms é uma macro só, não
duas configurações brigando pelo mesmo intervalo. Dá para adicionar e remover
linha com a macro rodando, e o `−` tira uma; a última fica. Duas linhas na
mesma tecla brigariam pelo estado dela, então só a primeira roda, e a linha de
status avisa.

Uma coisa esperada no `Segurar`: manter uma tecla normal pressionada aciona a
repetição automática do próprio sistema, igualzinho a segurar no teclado.
Modificadores (Shift, Ctrl, Alt, Super) não repetem, então ficam limpos.

### Atalhos (estilo AutoHotkey)

O que o AutoHotkey faz no Windows, para o Wayland: cada **macro** tem o seu
atalho e roda de qualquer janela, com o foco onde estiver, mesmo com o WayClick
parado. Vários convivem, como vários scripts `.ahk` rodando juntos, e cada um
ganha uma marca na lista enquanto roda (mais um ⚡ na linha de status e o
ícone da bandeja animado).

Um macro é

- um **atalho**: qualquer tecla ou botão do mouse, definido apertando,
  opcionalmente com Ctrl, Shift, Alt ou Super. Modificador a mais não
  atrapalha: um macro no `F1` continua disparando enquanto você segura Shift
  para correr. Quando dois macros dividem a tecla, `Ctrl+F1` ganha de `F1`
  enquanto o Ctrl está apertado.
- **Esconder o atalho dos outros programas**: ligado por padrão, como no
  AutoHotkey, então o jogo nunca vê o `F1` que você apertou. Desligado, a
  tecla também faz o que faria normalmente (o prefixo `~` do AutoHotkey).
- um **modo**: *Liga e desliga* (aperte para começar, de novo para parar),
  *Enquanto segura* (para assim que você solta), *Uma vez por aperto*, ou
  *Sempre* (sem atalho, roda sempre que os macros estão habilitados; bom para
  uma tecla contra inatividade a cada minuto).
- **Repetir os passos a cada N ms**, contado de início a início como o
  `SetTimer`, ou assim que acabarem. **Tempo de cada toque** é quanto cada
  toque fica apertado; *automático* é 40 ms ou metade do tempo de repetição,
  porque jogos leem as teclas uma vez por quadro e perdem toque curto demais.
- **passos**, em ordem: tocar uma tecla ou clicar (N vezes), segurar, soltar,
  esperar (exato, ou um tempo aleatório entre dois valores), digitar texto,
  girar a roda, mover o ponteiro uma distância, e os
  [passos de tela](#passos-de-tela).

O que um macro segura é solto quando ele para, quando você o desabilita e
quando o WayClick fecha. Um modificador que você segura para o atalho é solto
antes dos passos, como o AutoHotkey faz, então `Alt+A` mandando `Ctrl+C` chega
como `Ctrl+C` e não `Ctrl+Alt+C`. Abrir o editor pausa os macros, para dar
para escolher um atalho que um macro rodando já usa.

O **atalho de Suspend** embaixo da lista (nenhum por padrão) faz o que o
`F7::Suspend` faz num script: um aperto desliga os macros de atalho, então as
teclas deles voltam a chegar ao jogo como teclas comuns, e o aperto seguinte
liga de novo, com o mesmo som do atalho de armar. O jogo nunca recebe a
própria tecla de Suspend, os modificadores valem como num macro (`Ctrl+F7`
deixa um `F7` sozinho em paz), e aparece um aviso quando ela também é a tecla
de um macro ou o atalho de armar e desarmar. Uma diferença do AutoHotkey: lá o
Suspend não mexe num macro que já está rodando (isso é o `Pause`), e no
WayClick ele também para os macros rodando e solta o que eles seguram, então
um bhop que está disparando uma tecla para no instante do aperto.

**Importar AHK…** lê um arquivo `.ahk` (v1 ou v2) e transforma os atalhos dele
em macros, mostrando antes no que cada um virou e o que não deu para converter,
com o número da linha. Não é um interpretador de AutoHotkey; ele reconhece os
padrões que scripts de jogo usam:

| No script | Vira |
|---|---|
| `F1::` + `toggle := !toggle` + `SetTimer, Label, 50` / `Off` | Liga e desliga, a cada 50 ms |
| `SetTimer(Func, t ? 50 : 0)`, `% toggle ? 1 : "Off"` | Liga e desliga, a cada 50 / 1 ms |
| `while toggle { ... }`, `Loop { if !toggle break ... }` | Liga e desliga, passos em laço |
| `while GetKeyState("X", "P") { ... }`, `if state = U break` | Enquanto segura |
| `Send {RButton down}`, `KeyWait`, `{RButton up}` | Enquanto segura, botão apertado |
| `a::b`, `CapsLock::Ctrl` | Enquanto segura, um remapeamento |
| `F5:: Send ..., Click, Sleep, Loop 3 { }` | Uma vez por aperto |
| `SetTimer` na seção de auto execução | Sempre |
| `F7::Suspend`, `Suspend, Toggle`, `Suspend(-1)` | o atalho de Suspend |
| `PixelGetColor, c, X, Y` + `if (c = 0x...)` | Conferir a cor de um pixel, se sim / se não |
| `PixelSearch, Px, Py, X1, Y1, X2, Y2, cor, variação` + `if !ErrorLevel` + `Click %Px%, %Py%` | Conferir a cor de um pixel naquela área, clicando onde achou |
| `ImageSearch, X, Y, X1, Y1, X2, Y2, *40 arquivo.png` + `if (ErrorLevel = 0)` + `Click` | Achar uma imagem naquela área, clicar nela, senão o outro caminho |
| v2 `if PixelSearch(&x, &y, ...)`, `if !ImageSearch(...) return`, `achou := ImageSearch(...)` | os mesmos passos |
| `Send` com `{Tecla down}`, `{Tecla up}`, `{Tecla N}`, `^!+#`, texto, `{Click}`, `{WheelDown N}`; `Click`, `Sleep`, `Random`, `MouseMove ..., R`, `Gosub`, funções, atalhos empilhados, `$`, `*`, `~`, nomes `sc`/`vk`, `SetKeyDelay` | os passos correspondentes |

Hotstrings, atalhos `Tecla Up`, combinações `A & B`, condições de janela `#If`
(o atalho passa a valer em todas as janelas) e programação em geral são
avisados e ignorados. `Click X, Y` e `MouseMove X, Y` viram passos
[Ir a uma posição da tela](#passos-de-tela), com um aviso quando o script
media a partir da janela ativa, que é o padrão do AutoHotkey.

Os comandos de tela seguem as regras do AutoHotkey, das páginas
[PixelGetColor](https://www.autohotkey.com/docs/v1/lib/PixelGetColor.htm),
[PixelSearch](https://www.autohotkey.com/docs/v1/lib/PixelSearch.htm) e
[ImageSearch](https://www.autohotkey.com/docs/v1/lib/ImageSearch.htm). No v1
uma cor é azul, verde, vermelho (`0x0000FF` é vermelho) a menos que o comando
diga `RGB`, e o v2 usa sempre vermelho, verde, azul. O `PixelSearch` do v1
procura coluna por coluna a partir da esquerda a menos que tenha `Fast`, e
`X1 > X2` ou `Y1 > Y2` procuram a partir do outro lado; a variação vira a
tolerância do passo. A variação `*n` do `ImageSearch` (0 a 255) vira uma
semelhança (`*40` dá 87 %), e a imagem é procurada ao lado do script.
`A_ScreenWidth`, `A_ScreenHeight` e os nomes `A_VirtualScreen` querem dizer a
tela toda. O `if`/`else` depois da busca vira o *Se acontecer* e o *Se não* do
passo, inclusive cadeias de `else if`, `break` e `return`. Duas coisas são
diferentes: o WayClick clica no centro da imagem achada, onde o AutoHotkey dá
o canto de cima à esquerda, e um clique numa variável que nenhuma busca
preencheu é pulado com um aviso.

O script de bhop que um amigo escreveu em Python para Linux (segurar Espaço
aperta F9 a cada 15 ms, F7 pausa) é um script de três linhas no AutoHotkey, e
importa como *Space, enquanto segura, tocar F9, esperar 15,6 ms* mais o atalho
de Suspend F7:

```ahk
F7::Suspend
*$Space:: while GetKeyState("Space", "P") {
    Send {Blind}{F9}
    Sleep 15
}
```

Um laço assim segura cada aperto pela menor espera dele (15,6 ms aqui) em vez
dos 40 ms de sempre, então uma volta leva 31,2 ms, umas 32 vezes F9 por
segundo, que são os 30 ms do original e o que o Roblox lê (mais ou menos uma
tecla por quadro a 60 fps, veja abaixo).

O script anexado ao primeiro pedido desta função importa como *F1, liga e
desliga, a cada 15,6 ms, tocar E*:

```ahk
F1::
toggle := !toggle
if (toggle)
        SetTimer, SpamE, 1
else
        SetTimer, SpamE, Off
return

SpamE:
Send, e
return
```

Por que 15,6 e não 1: no Windows o AutoHotkey arredonda os períodos do
`SetTimer` e as esperas do `Sleep` para cima, para um múltiplo de 10 ou 15,6 ms
(as notas *Timer precision* nas páginas
[SetTimer](https://www.autohotkey.com/docs/v2/lib/SetTimer.htm) e
[Sleep](https://www.autohotkey.com/docs/v2/lib/Sleep.htm) da documentação),
então esse script apertava E umas 64 vezes por segundo. A importação mantém o
que o script fazia lá: timer ou espera abaixo de 15,6 ms vira 15,6 ms (`Sleep 0`
continua 0), e o relatório avisa. Mais rápido não ajudaria num jogo, que lê as
teclas mais ou menos uma vez por quadro e enfileira o resto: no Roblox (Sober a
60 fps, veja `tests/t_sober_kwin.py`) meio segundo de E a 1 ms continuou
chegando por 18 segundos depois de desligar o macro. A 15,6 ms todo E ainda
chega, só que a fila é bem menor, e a 33 ms não sobra nada. O editor de macro
ainda aceita 1 ms (medido: mediana de 1,000 ms entre toques) para um programa
que leia nesse ritmo.

No terminal, `wayclick --import script.ahk --dry-run` mostra o mesmo relatório,
e sem `--dry-run` acrescenta os macros na config (com o WayClick fechado, já
que um WayClick aberto grava a própria lista ao sair).

### Passos de tela

Mais cinco tipos de passo cobrem o que scripts do AutoHotkey fazem com
`Click X, Y`, `ImageSearch`, `PixelSearch`, `PixelGetColor` e `Run`.

| Passo | O que faz |
|---|---|
| **Ir a uma posição da tela** | Põe o ponteiro num pixel exato e clica (esquerdo, direito, do meio ou duplo) ou só move. *Em curva, como a mão* desliza até lá no tempo que você definir em vez de pular, e *Desvio aleatório* cai até essa quantidade de pixels ao lado a cada vez. |
| **Achar uma imagem na tela** | Procura uma imagem em todas as telas, ou só dentro de uma área que você marcar, com *Semelhança* de 50 a 100 % (85 por padrão). Achando, pode apontar para ela ou clicar no centro dela. |
| **Conferir a cor de um pixel** | Lê um ponto e compara com uma cor, vermelho, verde e azul cada um dentro da *Tolerância*, como o `PixelGetColor`. Com uma área marcada (*Procurar em*) procura a cor em qualquer lugar dentro dela, como o `PixelSearch`, e pode clicar onde achou: linha por linha a partir do canto de cima à esquerda, ou coluna por coluna quando veio de um script v1. |
| **Enviar um aviso** | Uma notificação do desktop. Com um webhook do Discord (configurações do canal, Integrações) ele posta naquele canal, e pode anexar um print de cada tela. |
| **Abrir um link ou arquivo** | Abre com o programa padrão, então páginas da web, links `roblox://` e `steam://` e arquivos funcionam. |

Os passos de imagem e de cor podem esperar. *Esperar até* aparecer ou sumir
(para uma cor, ter a cor ou perder a cor), por no máximo o tempo que você
definir, ou *olhar uma vez*. *Se acontecer* e *Se não* escolhem o que vem
depois, que pode ser seguir, parar o macro, recomeçar do passo 1 ou ir a um
passo pelo número. Isso basta para o laço de sempre de um macro de jogo, que é
esperar o botão, clicar, esperar a rodada acabar, coletar e recomeçar. Por
padrão um passo que não vê o que esperava para o macro, então nada é clicado
às cegas.

**Escolher na tela…** preenche o passo a partir da sua tela. Depois de uma
contagem (*Capturar após*, 3 s por padrão) que dá tempo de trocar para o jogo,
o WayClick tira uma foto de cada tela e mostra. Clique no ponto, e num passo de
cor a cor vem junto, ou arraste um retângulo em volta da imagem que vai ser
procurada. As imagens ficam em `~/.local/share/wayclick/images`, e *Abrir
arquivo…* aceita um PNG, JPEG, BMP ou WebP no lugar.

Na primeira vez que um passo precisa da tela, o sistema pergunta quais telas o
WayClick pode ver, no mesmo diálogo de compartilhar tela que as chamadas de
vídeo usam. O WayClick pede ao portal para lembrar a resposta e guarda o token
que volta em `~/.config/wayclick/screen-token`, que só você pode ler, então
onde o desktop suporta isso as próximas vezes pulam a pergunta. O
compartilhamento só fica aberto enquanto um macro precisa dele e fecha depois
de 30 s sem uso, então o indicador de compartilhamento no painel só aparece
enquanto algo está olhando.

As posições seguem o arranjo das Configurações de tela, todas as telas juntas,
com 0, 0 no canto de cima à esquerda do arranjo inteiro. Com escala elas ficam
em unidades escaladas, então uma tela 4K a 150 % conta como 2560 de largura. O
AutoHotkey mede o `Click X, Y` a partir da janela ativa por padrão, o que dá no
mesmo para um jogo em tela cheia na tela mais à esquerda.

Um limite para saber. Enquanto um jogo mantém o cursor travado, numa câmera em
primeira pessoa ou enquanto você arrasta para virar, o KWin não move o
ponteiro de jeito nenhum, então um passo de posição não faz nada ali. Use
posições onde o jogo mostra o cursor livre, como menus, lobbies, lojas e telas
de construir.

### Farm: vigiar, janela, contar, gravar, compartilhar

Mais quatro peças deixam um macro farmar partidas sozinho, como os macros
de tower defense para Windows fazem, mas só olhando a tela e apertando
teclas como uma pessoa. O WayClick nunca lê nem mexe na memória de um jogo.

| Passo ou ajuste | O que faz |
|---|---|
| **Vigiar a tela ou uma janela** | Continua olhando enquanto os próximos passos rodam, como um `SetTimer` com `ImageSearch` no AutoHotkey, atrás de uma imagem, da cor de um pixel ou de uma janela, a cada N segundos. Quando vê o que espera, pode clicar e seguir, ou levar o macro para outro lugar (recomeçar, parar, ir a um passo). Ele olha enquanto o macro espera (passos de espera, buscas na tela) e entre um passo e outro, nunca no meio de uma tecla apertada. Quando um vigia leva o macro para outro passo, todos os vigias param até o macro passar por eles de novo. |
| **Conferir ou focar uma janela** | Acha uma janela pelo nome do app (`Sober` bate com `org.vinegarhq.Sober`) ou pelo título inteiro, pode esperar ela abrir ou fechar, e pode trazer para a frente, minimizada ou não. Um título que só contém o texto não conta, então com o jogo fechado um terminal ou uma aba do navegador sobre ele não é confundido com o jogo. Precisa do KDE Plasma, porque só o KWin conhece as janelas. |
| **Contar** | Soma 1 cada vez que o macro passa, e a cada N vezes pode parar o macro ou ir a um passo. |
| **Parar após N rodadas** | No próprio macro. Um macro que repete para sozinho depois de tantas rodadas, e uma rodada que recomeça não conta. |

Os avisos preenchem `{run}` (o número da rodada), `{time}` (há quanto tempo o
macro está rodando, como `1:02:05`) e `{vitorias}` para um contador chamado
vitorias.

**● Gravar…** no editor de macro transforma o que você joga em passos. Depois
de uma contagem ele anota teclas, botões do mouse e a roda até você apertar a
tecla de parar (F10 por padrão, que o jogo não vê), e mostra os passos antes
de pôr depois do selecionado. Um aperto rápido vira um toque, um clique
guarda onde aconteceu (a posição vem do KWin), um aperto que andou vira um
arrasto, giros seguidos da roda viram um passo, e as pausas entre as ações
viram esperas em passos de 10 ms, ou ficam de fora. Movimento do mouse sem
clique não é gravado.

**Assistente de farm…** na aba Atalhos monta um farm inteiro de uma vez. Você
captura os botões do lobby, escolhe uma estratégia (um macro que você montou
ou gravou, que coloca e melhora as torres) e captura o botão que aparece no
fim da partida, mais um botão de pular onda e um de reconectar, se o jogo
tiver. Ele monta um macro comum que

1. confere se a janela do jogo está aberta e, se não estiver, abre o link
   que você deu e espera por ela,
2. traz o jogo para a frente e fica vigiando a janela fechar e o botão de
   reconectar, e qualquer um dos dois recomeça o farm,
3. clica nos botões do lobby em ordem (quando o primeiro não aparece, uma
   partida já está rolando, então vai direto para a estratégia),
4. joga a estratégia, clicando no botão de pular sempre que ele aparece, e o
   fim da partida corta a estratégia,
5. clica no botão do fim, posta o relatório no seu webhook do Discord, com
   print se você quiser, e começa de novo, até o número de partidas que você
   definir.

Um link de página de jogo do Roblox vira `roblox://placeId=ID`, o formato da
página sobre deep links do Roblox Creator Hub, e o Sober se registra para
links `roblox://`. Abrir um jogo assim ainda não foi testado com o Sober; se
não fizer nada, cole um link que funcione para você ou deixe o campo vazio. O
que o assistente monta dá para editar depois como qualquer outro macro, e o
WayClick não vem com estratégias prontas. Confira as regras do jogo antes,
porque muito jogo online bane farm automático.

**Exportar…** salva o macro selecionado num arquivo `.wayclick` com as
imagens dentro, então uma estratégia pode ser compartilhada como um arquivo
só, e **Importar…** adiciona os macros de um arquivo desses. O webhook do
Discord nunca vai para o arquivo, e webhooks num arquivo são descartados na
importação de qualquer jeito, porque um arquivo de outra pessoa poderia
mandar prints da sua tela para o canal dela. As imagens só são salvas quando
são imagens de verdade, e nunca fora da pasta de imagens. Um passo que
abriria qualquer coisa que não seja um link da web, `roblox://` ou
`steam://` vira uma espera vazia, para os outros passos manterem os números,
e os macros importados chegam desligados.

### A Doca

Os macros de tower defense do Windows jogam numa visão própria, para o PC
continuar usável enquanto farmam. **A Doca** faz o mesmo no KDE Plasma: um
KWin só dela que o WayClick abre numa janela, com o jogo dentro. Macros
marcados com **Jogar na Doca** funcionam só lá:

- teclas, cliques, a roda e arrastos vão para o jogo na Doca, nunca para a
  janela da frente, e o ponteiro e o foco do seu desktop ficam onde estão;
- passos de tela, imagem, cor e janela olham a Doca, sem diálogo de
  compartilhar tela;
- a janela da Doca pode ficar atrás de outras janelas ou minimizada, e o
  jogo continua rodando e o macro continua vendo (medido com um jogo que
  espera o vsync no `tests/t_dock_hidden.py`, 60 quadros por segundo na
  frente, coberto e minimizado);
- as posições são as da tela da Doca, com 0,0 no canto de cima à esquerda, e
  a Doca mantém o tamanho escolhido (1280×720 por padrão; redimensionada,
  ela volta), então os pontos de um macro são os mesmos onde quer que a
  janela esteja;
- um link `roblox://` num macro da Doca abre dentro da Doca, e links da web
  abrem no seu desktop como sempre.

Na aba **Doca** escolha o **Tamanho** e o comando do **Jogo** (`flatpak run
org.vinegarhq.Sober` por padrão) e aperte **Abrir a Doca**, e o jogo abre
dentro dela. **Abrir o jogo** abre de novo depois que você fecha, abrindo a
Doca antes se precisar. **Mostrar** traz a janela da Doca para a frente, e a
aba mostra ● enquanto a Doca está aberta. A Doca é uma janela comum, então
você também pode clicar nela e jogar você mesmo. No editor de macro e no
Assistente de farm marque **Jogar na Doca**; **● Gravar…** e **Escolher na
tela…** passam a funcionar na Doca também. Fechar a janela da Doca, ou
**Fechar a Doca**, fecha o jogo e para os macros dela, e um macro da Doca
iniciado com a Doca fechada avisa e não roda.

**Conta** escolhe qual Sober a Doca abre, o de sempre ou uma das
[Contas do Sober](#contas-do-sober), e os outros jogos não usam isso. O seu
Sober de sempre abre uma cópia por vez, então feche ele antes de abrir na
Doca, ou escolha uma conta. A página Doca avisa quando o Sober que ela
abriria já está aberto em outro lugar. O Sober só recebe um link `roblox://`
ao abrir, então o link de um macro da Doca não é aberto enquanto uma conta
está aberta na Doca, e a Doca avisa. A Doca precisa de
uma sessão Wayland do Plasma (`kwin_wayland`) e do `dbus-daemon`, que vêm
com o Plasma.

### A aba Tower Defense

Os macros de tower defense do Windows mostram o jogo dentro da janela deles
e farmam de lá. A aba **Tower Defense** faz o mesmo com a Doca: o jogo da
Doca aparece no meio da aba umas 20 vezes por segundo, e mouse, roda e
teclas em cima dele vão para o jogo, então dá para jogar pela aba também. O
farm só olha a tela da Doca e aperta teclas e clica nela. Ele nunca lê nem
mexe na memória do jogo.

| Parte | O que tem |
|---|---|
| Esquerda | **Iniciar (F4)**, **Parar (F5)** e **Pausar (F6)**, o que o farm está fazendo, há quanto tempo ele roda, partidas, vitórias e derrotas, **Abrir a Doca**, **Encaixar o jogo** (minimiza a janela da Doca enquanto o jogo continua rodando na aba) e **Desencaixar**, **Entrar no jogo** (abre o link do jogo na Doca) e o registro |
| Meio | O jogo. **Alinhar a câmera** afasta e arrasta para baixo como no começo de cada partida, e **Mostrar os lugares** marca onde cada torre vai |
| Direita | As abas **Estratégia**, **Telas** e **Configurações** |

Nesta aba a janela do WayClick cresce para 1600×900 (menos numa tela menor)
e volta ao tamanho dela quando você sai da aba. A visão para enquanto a aba
está escondida e cai para 5 quadros por segundo enquanto o WayClick está
atrás de outra janela.

**Estratégia.** Uma estratégia tem nome, observações, cinco torres com os
lugares onde cada uma vai, uma linha do tempo e o lobby.

- **Marcar** numa torre, e depois clique no jogo onde ela vai. O clique
  direito tira um lugar e o Esc termina.
- **● Gravar** alinha a câmera e depois anota o que você joga na visão. 1 a
  5 pega uma torre e um clique coloca ela (a até 8 px de um lugar que a
  torre já tem, é esse lugar), clicar numa torre e depois E ou Z melhora
  ela no caminho de cima ou no de baixo, Q larga a torre da mão, e F10
  desfaz a última ação. Os tempos contam de quando a gravação começa, então
  comece ela quando a partida começar.
- **Adicionar…**, **Editar…** e **Remover** mudam a linha do tempo à mão,
  com colocar, melhorar em qualquer caminho, vender, apertar uma tecla,
  clicar numa imagem e esperar uma imagem.
- **Lobby** guarda os botões clicados em ordem para entrar numa partida, e
  um macro do lobby (um da aba Atalhos) que toca antes deles, como andar
  até um elevador.
- **Exportar…** e **Importar…** compartilham uma estratégia num arquivo
  `.wayclick` só, com as imagens. Um arquivo de estratégia dado à
  importação da aba Atalhos é oferecido a esta aba, e um arquivo de macros
  dado aqui aponta para a aba Atalhos.
- Uma estratégia guarda o tamanho da Doca em que foi feita, e a aba avisa
  quando a Doca está em outro tamanho, já que os lugares são posições na
  tela.

**Telas.** Imagens do jogo, capturadas da Doca com **Capturar…** enquanto
ela mostra.

| Imagem | O que o farm faz com ela |
|---|---|
| Partida começou | Opcional. O relógio da estratégia começa quando ela aparece, e sem ela logo depois do lobby |
| Torre colocada | Um pedaço do painel que abre quando uma torre é colocada. Com ela, uma torre que não foi colocada, como quando ainda falta dinheiro, é tentada de novo |
| Pular onda | Opcional. Clicada sempre que aparece durante a partida |
| Vitória, Derrota | Algo que só aparece quando a partida é ganha, ou perdida. Conta uma vitória ou uma derrota |
| Reconectar | Opcional. Clicada sempre que aparece, e o farm recomeça do lobby |
| Botão de vender | Opcional. Clicado para vender depois de clicar na torre. Sem ele, a tecla de vender das Configurações é apertada |
| Depois da partida | Os botões clicados em ordem quando a partida acaba, como o de jogar de novo. Sem as imagens de Vitória e Derrota, o primeiro também é como o fim da partida é visto |

Nas configurações do jogo desligue Camera Shake e Low Quality Rings, ligue
Show Tower Options (o painel de onde vem a imagem de Torre colocada), deixe
Cancel Placement no Q e a tecla de Switch Hotbar fora do Q, e mantenha o UI
Scale como estava quando você capturou as imagens.

**Configurações.**

| Ajuste | O que faz |
|---|---|
| Janela do jogo | O nome do app da janela do jogo na Doca, `Sober` por padrão |
| Link do jogo | Abre o jogo na Doca com **Entrar no jogo**, e de novo quando o jogo fecha. A página de um jogo do Roblox vira `roblox://placeId=ID` e o link de um servidor privado (`…?privateServerLinkCode=…`) vira `roblox://placeId=ID&linkCode=CÓDIGO`, as duas formas da [página do Roblox sobre deep links](https://create.roblox.com/docs/production/promotion/deeplinks); um link `roblox://` fica como veio |
| Esperas, Partida mais longa, Parar após | Quanto esperar cada botão do lobby e a partida começar, quanto uma partida pode durar antes de o farm seguir em frente, e quantas partidas jogar (nunca para, por padrão) |
| Velocidade das ações | Rápida, Equilibrada ou Lenta, as pausas entre apertar, clicar e olhar |
| Tentar colocar por até, Tentar de novo a cada | Por quanto tempo uma torre que não foi colocada é tentada de novo, e de quanto em quanto tempo |
| Pular ondas até | Minutos desde o começo da partida depois dos quais o Pular onda fica de lado (a partida toda, por padrão) |
| Câmera | **Alinhar a câmera quando a partida começa** segura a tecla de afastar (O) por um tempo e depois arrasta para baixo com o botão direito, do meio da tela |
| Teclas do jogo | Melhorar no caminho de cima (E) e no de baixo (Z), vender (nenhuma), cancelar a colocação (Q) |
| Atalhos | Iniciar F4, Parar F5 e Pausar F6, em qualquer janela enquanto a Doca está aberta. Um macro com atalho ou o atalho do WayClick na mesma tecla vem primeiro |
| Relatório | Um webhook do Discord que recebe uma linha depois de cada partida, com print da tela se quiser, preenchida com `{run}`, `{result}`, `{wins}`, `{losses}` e `{time}` |

Links de compartilhar (`roblox.com/share?code=…`) são recusados. A
[página do Roblox sobre links de compartilhar](https://create.roblox.com/docs/production/promotion/share-links)
não dá a eles uma forma `roblox://`, então eles abririam no navegador, fora
da Doca. Use a página do jogo ou um link com `privateServerLinkCode`, ou
abra o servidor à mão na Doca antes de apertar Iniciar.

**Como um farm anda.**

1. A janela do jogo precisa estar aberta na Doca. Quando não está, o farm
   abre o link do jogo na Doca e espera até 2 minutos por ela.
2. Ele toca o macro do lobby, se houver, e clica os botões do lobby em
   ordem. Quando o primeiro não aparece, já tem uma partida rolando.
3. Ele espera a imagem de Partida começou, começa o relógio da estratégia e
   alinha a câmera.
4. Cada ação roda na hora dela. Colocar aperta a tecla da torre, clica no
   lugar dela e espera a imagem de Torre colocada. Quando ela não aparece, o
   Q cancela e a torre é tentada de novo, e o Pular onda continua sendo
   clicado enquanto isso. Melhorar clica na torre e aperta E ou Z.
5. Vitória, Derrota ou o primeiro botão de Depois da partida terminam a
   partida. O farm conta, manda o relatório, clica os botões de Depois da
   partida e recomeça, até o número de partidas que você escolheu.
6. Quando a janela do jogo fecha ou o botão Reconectar aparece, a rodada
   recomeça, e o jogo abre de novo pelo link. Cinco rodadas seguidas sem
   partida param o farm, já que as imagens provavelmente não batem com o
   jogo.

Pausar solta tudo e para o relógio da partida junto, então a estratégia
continua de onde estava.

Isso foi medido contra um tower defense de mentira num KWin aninhado
(`tests/t_tower_kwin.py`): duas partidas, com o jogo caindo na segunda e
abrindo de novo pelo link `roblox://`, e a visão a 20 quadros por segundo,
cada captura de uma Doca de 960×540 levando 2,9 ms, uns 7% de um núcleo
para o WayClick e 4% para o KWin da Doca. Ainda não foi testado no Tower
Defense Simulator de verdade, e nenhuma estratégia pronta vem com o
WayClick. Confira as regras do jogo antes, já que muitos jogos online banem
farm automático.

### Contas do Sober

O Sober abre uma cópia por vez. Uma segunda acha a primeira por uma porta
fixa do 127.0.0.1 e não abre, então no Linux duas contas do Roblox não
jogavam juntas como jogam no Windows. A página **Contas do Sober** dá a cada
conta um Sober só dela, com login do Roblox, configurações e rede próprios,
então ela abre ao lado do seu Sober de sempre e os dois jogam ao mesmo tempo.

Aperte **Adicionar conta…**, dê um nome e aperte **Abrir** (ou clique duas
vezes nela). Na primeira vez o Sober se prepara na conta nova e baixa o
Roblox de novo, e você entra com o login do Roblox lá. Depois disso ela abre
direto no Roblox. **Fechar** fecha só aquela conta, **Renomear…** muda o
nome, e **Remover…** apaga os dados do Sober dela neste computador (o login
do Roblox, as configurações e os downloads), enquanto a conta do Roblox em si
fica como está. Dá para ter até quatro contas. Uma conta que já estava aberta
antes de o WayClick abrir aparece aberta, e sair do WayClick deixa as contas
abertas rodando. Na página Doca, **Conta** abre uma delas na Doca.

Como funciona. Cada conta roda o Sober numa rede só dela, feita pelo
`pasta`, onde a porta do 127.0.0.1 do seu Sober de sempre não existe, e
nenhuma porta é repassada para dentro ou para fora. Lá dentro a pasta da
conta (`~/.local/share/wayclick/sober-accounts/<id>/app`) é montada sobre
`~/.var/app/org.vinegarhq.Sober` só para aquela cópia, e o `unshare` devolve
o seu próprio usuário ao Sober, mais um namespace de tempo só dele com os
mesmos relógios, antes de o `flatpak run` abrir ele como sempre. O Sober
trava trechos do `/proc/self/ns/time` (depois de um tempo aparecem umas cem
travas no `/proc/locks`), e uma conta que dividia esse namespace com o seu
Sober de sempre parava de 1 a 3 minutos depois de abrir em "status !=
SL_RESULT_SUCCESS", a mesma queda que o RAM-Linux lista, onde
[uma issue](https://github.com/broreirik/RAM-Linux/issues/1) propõe a mesma
correção. O seu Sober de sempre, a pasta dele e o escopo dele nunca são
tocados. Uma trava na pasta da conta diz se ela está aberta, e a rede anotada
ao abrir é como o WayClick acha aquela cópia no `flatpak ps` para fechar,
nunca a que está na rede do seu computador.

Isso foi medido com o Sober de verdade num KWin aninhado
(`tests/t_sober_accounts_kwin.py`). Duas contas chegaram ao Roblox com poucos
segundos de diferença e ficaram abertas lado a lado por 4 minutos, cada uma
numa rede e num namespace de tempo só dela, com o Sober gravando só na pasta
da conta e travando no namespace dela. Fechar uma deixou a outra
aberta, uma conta vazia abriu na primeira tela do Sober, e o Sober do
computador que rodou o teste não foi tocado.

Limites

- toda cópia é o mesmo app com o mesmo título de janela, então um passo
  *Janela* que procura o Sober acha qualquer uma delas;
- cada conta aberta usa a memória de um Roblox a mais;
- o Sober só recebe um link `roblox://` ao abrir, então um link para uma
  conta que já está aberta não é aberto (medido, um segundo Sober na mesma
  conta para em "An instance of Sober is already running");
- precisa do `pasta`, do util-linux 2.38 ou mais novo e do Linux 5.6 ou
  mais novo (namespaces de tempo), e um Sober futuro pode trazer
  verificações que isso não passa.

Quando o Sober de uma conta para, a página diz o porquê em vermelho, pela
linha `FATAL` do log dele (`launch.log` na pasta da conta), e o Sober deixa a
janela de erro dele aberta até você fechar.

A equipe do Roblox já chamou rodar mais de um cliente de exploit, dizendo
que hoje isso não é banido, mas que pode mudar
([DevForum, julho de 2025](https://devforum.roblox.com/t/is-fishstrap-bloxstrap-no-longer-allowed-on-the-platform/3829253/13)),
e o Sober não dá suporte a isso ([FAQ do Sober](https://vinegarhq.org/Sober/FAQ/index.html)).
Use por sua conta e risco.

### Acionamento

**Modo**

- `Sem gatilho (o atalho liga/desliga)` — o Start roda; o atalho também alterna.
- `Age enquanto o gatilho é segurado` — arme, e ele roda só enquanto você
  segurar o gatilho. Ao soltar, para, mas continua armado.

O modo segura **tudo o que estiver ligado**, não só o clique, e o botão logo
abaixo diz qual das duas coisas vai fazer: **Start** quando não há gatilho,
**Armar** quando há. A aba Gatilho ganha um ponto sempre que existe um gatilho
definido, do mesmo jeito que as outras marcam o que têm ligado.

**Gatilho** — o que você segura. Clique no campo e aperte: qualquer tecla do
teclado, ou qualquer botão do mouse, inclusive os dois do polegar. É um campo
só porque é uma pergunta só; qual dos dois é decide como o WayClick escuta, não
o que você precisa configurar. Botão passa pela mesma captura que o modo
segurar usa; tecla é lida do `/dev/input`, igual ao atalho.

"Segurar o clique esquerdo e apertar E dez vezes por segundo" é isso: Gatilho
em `clique Left`, a macro de teclado no `E` a cada 100 ms, clique desligado.

**O gatilho segura** — quais engines esperam o gatilho. Desmarque uma e ela roda
assim que você apertar Armar, enquanto as marcadas continuam esperando. Dá para
segurar o botão do mouse para clicar com a macro de teclado rodando o tempo
todo, ou o contrário.

**Atraso ao iniciar** — segundos antes de começar. Tire o cursor da janela
primeiro, senão o autoclicker clica no próprio botão Parar.

**Parar sozinho após** — para depois de N segundos (`nunca` = desligado).

**Atalho de armar/desarmar** — arma e desarma de qualquer janela, seja qual for
a que estiver em foco. Definido apertando, igual a todo o resto, então qualquer
tecla serve; o campo avisa quando você escolhe uma que também digita, porque
ela armaria e desarmaria toda vez que fosse apertada em qualquer lugar. `Esc`
sempre para, com a janela em foco. **Esconder dos outros programas** continua
armando e desarmando, mas a tecla não chega ao jogo, como o AutoHotkey esconde
os atalhos dele; vem desligado, para nada mudar para quem já usa. Quando um
macro sem modificadores usa a mesma tecla (um script importado com o próprio
`F8::`, por exemplo), a tecla é do macro enquanto os macros estão ligados, e o
clique não arma por ela; a aba Atalhos e o relatório da importação avisam. Um
macro em `Ctrl+F8` deixa o `F8` sozinho para o atalho de armar.

Ele não é o gatilho. Armar faz o gatilho valer, e o gatilho é quem roda as
coisas — que é o que permite "armar no F8 e rodar segurando o clique esquerdo"
serem duas configurações separadas e óbvias.

**Som ao acionar** — um bipe agudo ao ligar e grave ao desligar, para você saber
que o atalho pegou sem precisar olhar.

### Mandar tecla para uma janela

Escolha uma das suas janelas abertas e o WayClick alimenta ela com uma tecla no
intervalo definido — espaço num jogo a cada 60 s, por exemplo, enquanto você
continua trabalhando. A lista mostra cada janela aberta com o ícone real do
programa e uma marca de como a tecla vai chegar; a tecla se define apertando
ela, igual na macro:

| | |
|---|---|
| **⌨** | Janela X11 (Xwayland). A tecla é endereçada àquela janela: seu foco nunca é tocado, e funciona **mesmo minimizada**. |
| **◐** | Janela Wayland pura. Não há como endereçar, então o WayClick dá foco a ela por ~40 ms, manda a tecla e devolve o foco. |

Medido contra um alvo X11 em processo próprio, com o foco parado em outra janela
o tempo todo: 3 de 3 entregues sem foco, mais 6 com a janela minimizada, 0
vazaram para a janela em foco.

Se a janela escolhida for Wayland pura, o grupo mostra um aviso vermelho. A saída
é reabrir aquele programa como cliente X11, o que normalmente é uma variável de
ambiente:

```bash
SDL_VIDEODRIVER=x11 ./jogo          # SDL (a maioria dos jogos)
GDK_BACKEND=x11 ./app               # GTK
QT_QPA_PLATFORM=xcb ./app           # Qt
flatpak run --env=SDL_VIDEODRIVER=x11 org.exemplo.App
```

Aí ele passa a aparecer como ⌨ e recebe a tecla direto. Dois poréns honestos:
programa que lê input raw pode ignorar evento sintético do X11, e isto vale só
para teclado — clique segue o cursor, não o foco, então mirar clique numa janela
exigiria teleportar seu ponteiro.

### Atualizações

Instalado por pacote ou pelo `install.sh`, não existe lugar onde uma versão
nova se anuncie — então o WayClick pergunta ao repositório. Quando existe
release mais nova que a sua, aparece uma barra no topo com o botão **Baixar**,
que abre aquela release; o `×` esconde até a próxima abertura, de propósito: a
graça é justamente avisar **toda** vez que você abrir, até você atualizar.
Iniciado com `--tray`, o aviso chega como notificação da bandeja, já que não há
janela onde pôr a barra.

Pergunta primeiro ao GitHub e cai no GitLab — o projeto vive nos dois e
qualquer um responde sem login. A resposta fica em cache e a pergunta é feita
no máximo uma vez por dia, nunca na thread da interface, então rede ruim não
atrasa a janela abrir. **Ajuda > Procurar atualizações** pergunta na hora e
responde mesmo quando não há novidade; **Configurações > Procurar atualizações
ao abrir** desliga a checagem automática, e aí nada é requisitado.

Nada é baixado nem instalado por você: o botão abre a página da release e você
atualiza do jeito que instalou.

### Anti-AFK

Mexe o ponteiro alguns pixels e devolve para onde estava, a cada N segundos. É
independente do Iniciar/Parar — marque e ele roda, mesmo sem nada clicando, que
é justamente a graça: jogos e sites decidem que você está ocioso pelo
**movimento do ponteiro**, não pelos cliques, então só o autoclick ainda deixa
você tomar kick por AFK.

Dois detalhes fazem funcionar. Os dois movimentos vão separados por 50 ms,
porque mandados juntos o compositor somaria +4 e −4 no mesmo quadro e nada
pareceria ter se mexido. E o sentido inverte a cada ciclo: a aceleração do
ponteiro escala a ida e a volta com fatores um pouco diferentes, então elas não
se anulam sozinhas, e sem inverter o cursor iria andando pela tela — medido em
0,15 px por ciclo, centenas de pixels por hora. Alternando, ele fica preso entre
duas posições: **0,0000 px de deriva**.

O passo é de 4 unidades e não 1 porque a aceleração encolhe o deslocamento: com
o ponteiro desacelerado, 1 unidade virou 0,402 px de tela — nem um pixel
inteiro, o que um programa que lê coordenadas inteiras nunca perceberia. 4
unidades dão uns 2–3 px, o bastante para registrar em qualquer configuração e
ainda invisível, já que volta em 50 ms.

## Como funciona

O Wayland não tem, de propósito, uma API de "mande um clique naquela janela".
Então o WayClick age uma camada abaixo do compositor.

**Clicar** — o `/dev/uinput` cria um dispositivo de entrada de verdade no
kernel. O libinput reconhece, o compositor trata como hardware, e os cliques
caem onde o cursor estiver. Para chegar a 10.000 cliques/s, a sequência
press/SYN/release/SYN sai em um único `write()`, e o laço usa deadline absoluto:
dorme o grosso do intervalo e, só abaixo de 1 ms, queima as últimas centenas de
µs em busy-wait, porque a granularidade do sleep do sistema sozinha não alcança
precisão de sub-milissegundo. De 1 ms para cima ele só dorme (período medido
com mediana de 1,000 ms), porque busy-wait segura a GIL do Python e deixaria
sem vez a thread que repassa o seu teclado.

**Esconder um atalho.** O compositor não tem API para engolir uma tecla, então
o WayClick faz como o modo segurar faz com o mouse: uma thread de entrada lê
todos os teclados, e quando algum atalho precisa sumir ela pega um
`EVIOCGRAB` naquele teclado e repassa todo o resto por um clone virtual com o
mesmo nome, ids, teclas e LEDs (o KDE mantém o seu layout e o Caps Lock
continua acendendo). A captura só acontece com nenhuma tecla apertada, senão o
compositor veria o aperto e nunca o soltar, e o que ainda estiver apertado
quando ela solta é solto no clone. Atraso do relay medido em 0,04 ms de
mediana, também com um macro de 1 ms rodando. Todos os macros, o atalho de
armar e o modo segurar dividem esse único leitor, e é por isso que capturar o
teclado não deixa os outros surdos.

**Atalho global** — lê `/dev/input/event*` direto, único jeito de enxergar
teclas que não estão indo para a sua janela. Teclados criados por remapeadores
(keyd, kmonad, input-remapper) valem como fonte.

**Tecla por janela** — o Wayland entrega input para quem tem foco e não oferece
como endereçar uma surface; o `fake_input` do KWin, único protocolo de injeção
que ele implementa, também não tem argumento de surface. O X11 é o oposto: o
`XSendEvent` carrega a janela de destino e o cliente processa mesmo sem foco e
sem estar mapeado — por isso o caminho ⌨ funciona e o ◐ precisa pedir foco
emprestado. O WayClick lê a lista de janelas pelo scripting do KWin (única coisa
que enxerga janela Wayland) e os ids X11 direto do `_NET_CLIENT_LIST` via libX11.

**Modo segurar** — esse precisa de um truque. O compositor agrega o estado dos
botões por *seat*, então enquanto o botão físico está pressionado ele
**descarta todo clique injetado** — medido: 0 de 20 entregues. Por isso o
WayClick dá um `EVIOCGRAB` no mouse escolhido: o compositor deixa de ver o
device real e passa a ver só o virtual, e nós repassamos tudo — movimento, roda,
outros botões — filtrando apenas o botão-gatilho, que vira o fluxo de cliques. A
latência do relay ficou em 0,02 ms de mediana sob carga de 10.000 cliques/s,
então o ponteiro continua liso. A captura é liberada ao parar, ao sair, e pelo
kernel se o processo morrer.

O gatilho só é engolido quando o clique está ligado, que é quando o autoclicker
reemite no lugar dele. Segure o botão para acionar só a macro de teclado e ele é
repassado normalmente — você fica com o seu botão e com a macro.

**Ir a uma posição.** Um mouse relativo só diz quanto andar, e a aceleração do
ponteiro muda quanto. Então, para os passos de tela, o WayClick cria mais um
device, um ponteiro absoluto como o mouse que uma máquina virtual dá ao
convidado, com `ABS_X` e `ABS_Y` de 0 a 65535 e três botões. O udev marca como
mouse, o libinput entrega ao KWin como movimento absoluto, e o KWin espalha
essa faixa pela área de trabalho inteira, todas as telas juntas. O WayClick
transforma a posição no valor que cai no centro daquele pixel, a partir do
arranjo de telas que o Qt informa, e acompanha o arranjo quando as telas
mudam. Conferido pelo próprio libinput em três arranjos, cada canto e cada
borda caem no pixel exato. O kernel descarta um valor igual ao anterior, então
ir de novo ao mesmo ponto primeiro dá um passo de uma unidade para o lado. O
device só é criado quando um macro habilitado precisa dele.

**Ver a tela.** No Wayland só o compositor tem a imagem, e a porta de entrada
para um programa comum é o portal ScreenCast do desktop, o mesmo das chamadas
de vídeo. O WayClick fala com ele numa conexão D-Bus própria, pede todas as
telas sem o cursor, e lê os quadros pelo PipeWire e pelo GStreamer
(`pipewiresrc`, `videoconvert`, `appsink`), dez por segundo. Achar uma imagem
é correlação cruzada normalizada (o `TM_CCOEFF_NORMED` do OpenCV) escrita em
numpy com FFT. Os candidatos saem da imagem em cinza, na metade ou num quarto
do tamanho quando o template aguenta a redução (texto miúdo e ruído não
aguentam, e são procurados no tamanho real), e cada um depois é pontuado nas
três cores e pela cor média, então o mesmo botão apagado, ou o mesmo ícone em
outra cor, não conta. Medido em telas sintéticas de 1920×1080
(`tests/bench_find_image.py`), 284 de 288 recortes aleatórios de 16×16 a
300×150 foram achados no lugar exato, com mediana entre uns 30 e 60 ms por
tela. Os outros quatro caíram numa cópia igual, duas áreas lisas da mesma cor
e dois recortes de 16 px com um gêmeo de 0,999.

**Janelas e o cursor.** No Wayland só o compositor conhece as janelas, então
os passos de janela e o gravador perguntam ao KWin. O WayClick carrega um
script pequeno no KWin pelo D-Bus (`org.kde.kwin.Scripting`), e o script lê
`workspace.windowList()` ou `workspace.cursorPos` e chama de volta um serviço
D-Bus do WayClick com a resposta. Medido contra um KWin aninhado
(`tests/t_farm_kwin.py`), uma pergunta leva de 5 a 10 ms, e um passo de
janela pergunta no máximo uma vez por segundo.

**A Doca.** A Doca é o `kwin_wayland --wayland-display $WAYLAND_DISPLAY
--socket wayland-wayclick-dock` com largura e altura fixas, então ele roda
como uma janela do seu desktop (o backend Wayland do KWin), ao lado de um
`dbus-daemon` só dele. Configuração, estado e cache ficam em
`$XDG_RUNTIME_DIR/wayclick-dock`, então a configuração de telas que ele grava
nunca encosta na do desktop, e só o layout do teclado e as configurações de
entrada (`kxkbrc`, `kcminputrc`) são copiados para lá. O kwinrc dele maximiza
janelas novas sem borda, para o jogo encher a tela, e desliga os cantos de
tela e a Visão geral, que um macro num canto abriria. O jogo roda com
`WAYLAND_DISPLAY` apontando para a Doca e sem `DISPLAY`, com o resto do seu
ambiente, o D-Bus da sessão inclusive, então portais e chaveiro funcionam
como sempre. Os macros falam direto com esse KWin. A entrada vai pelo
`org_kde_kwin_fake_input` no socket da Doca, o mesmo caminho de um device de
verdade dentro do KWin, então o desktop nunca vê nada, e a tela vem do
`org.kde.KWin.ScreenShot2.CaptureWorkspace` no D-Bus da Doca (uns 5 ms para
1280×720, também com a janela escondida). Passos de janela rodam scripts do
KWin nesse D-Bus. Um script pequeno no KWin do desktop acha a janela da Doca
pelo processo dela para manter o tamanho, trazer para a frente e fechar.
Medido no KWin aninhado do `tests/t_dock_kwin.py`, a Doca abre em uns 0,2 s.

**A aba Tower Defense.** A visão é um widget que pede ao KWin da Doca a
mesma captura do ScreenShot2 até 20 vezes por segundo, de uma thread
própria, e desenha ela escalada na aba, mantendo o formato. Apertar, soltar,
mover, a roda ou uma tecla em cima dela vira o pedido certo do
`org_kde_kwin_fake_input` no socket da Doca, no ponto da Doca embaixo do
ponteiro, e com o botão direito ou o do meio segurado o movimento vai
relativo, já que um jogo pode travar o ponteiro enquanto gira a câmera. As
teclas que o farm usa como atalho não são mandadas. O farm é um executor de
macro da Doca só dele, com o relógio da partida, vigias do fim da partida, da
janela fechada, do reconectar e do pular onda, e esperas em fatias de um
quarto de segundo, para Pausar e Parar valerem na hora. Nada nele lê a
memória do jogo nem a do Sober.

## Problemas comuns

**"Atalho global DESLIGADO"** — você não está no grupo `input`, ou a sessão
começou antes da mudança de grupo. Faça os passos 3 e 4 acima e saia/entre de
novo.

**Permissão negada em `/dev/uinput`** — falta a regra de udev do passo 3, ou o
módulo `uinput` não está carregado (passo 2). Confira com `ls -l /dev/uinput`.

**Clica e não acontece nada / desliga sozinho** — o cursor estava em cima da
janela do WayClick quando começou. É para isso que serve o atraso ao iniciar.

**Sites travam em taxas muito altas** — não é a injeção: navegador processa
entrada numa única thread de JS e não dá conta de 10.000 eventos/s. Na web, de
1 a 5 ms (200 a 1000 cliques/s) é o teto útil. Programas nativos aguentam muito
mais.

**A sensibilidade muda no modo segurar** — confira se o mouse selecionado na
interface é o que você está segurando de fato; o clone copia a identidade
daquele device, e é nela que o KDE guarda as configurações por dispositivo.

**Tomei kick de AFK mesmo com o clique rodando** — detecção de ociosidade
costuma olhar movimento do ponteiro, não cliques. Ligue o Anti-AFK. Alguns jogos
vão além e checam se o personagem se moveu; nesse caso use também a macro de
teclado apertando uma tecla de movimento.

**Um passo de posição não mexe o cursor no jogo.** O jogo travou o ponteiro
(câmera em primeira pessoa, ou enquanto você vira), e o KWin não move um
ponteiro travado. Use o passo onde o jogo mostra o cursor.

**Um passo de imagem nunca acha a imagem.** Capture de novo com o jogo na
resolução e na escala em que ele roda, porque a busca não redimensiona, e
deixe de fora partes que mudam, como contadores ou fundos animados. Baixar um
pouco a *Semelhança* ajuda com compressão e efeitos de brilho, e marcar uma
área de busca deixa mais rápido e tira da frente o que é parecido em outro
lugar.

**Os passos de tela dizem o que instalar.** Instale os pacotes que a mensagem
lista e reabra o WayClick. O AppImage não consegue carregar esses pacotes,
então para passos de tela use um pacote ou o `install.sh`.

**A pergunta de compartilhar a tela volta toda vez.** O desktop não guardou a
permissão, ou recusou o token salvo. O WayClick salva o token que voltar a
cada vez, e apagar `~/.config/wayclick/screen-token` faz ele perguntar do
zero.

## Testes

Os arquivos em `tests/` são verificações executáveis, não testes unitários —
eles criam dispositivos de entrada falsos e medem comportamento real.

Estes são isolados e podem rodar enquanto você usa o desktop, até com
um jogo aberto: o WayClick só abre devices com `wctest` no nome, o teclado e o
mouse falsos são capturados antes do primeiro evento, e todo device virtual
que o WayClick cria é capturado pelo teste no instante em que aparece, que é
também como o teste lê o que teria chegado ao compositor (veja
`tests/harness.py`). Sem root.

```bash
python3 tests/t_ahk_import.py                       # o importador nos scripts de tests/ahk
python3 tests/t_ahk_screen.py                       # PixelGetColor, PixelSearch, ImageSearch e Suspend vindos de um .ahk, numa tela falsa
sg input -c "python3 tests/t_suspend_ui.py"         # o atalho de Suspend: o script de bhop, liga, desliga, Ctrl, avisos, config, --import
python3 tests/t_ahk_kwin.py                         # um .ahk de ponta a ponta num KWin aninhado, com duas capturas de tela
sg input -c "python3 tests/t_hotkey_macros.py"      # SpamE F1.ahk a 15,6 e a 1 ms, todos os modos, esconder, relay
sg input -c "python3 tests/t_hotkeys_ui.py"         # a aba Atalhos de ponta a ponta, config, --import
sg input -c "python3 tests/t_hotkeys_dialogs.py"    # os editores de macro e de passo
sg input -c "python3 tests/t_clicker_timing.py"     # período do Clicker e relay do teclado juntos
sg input -c "python3 tests/t_screen_macros.py"      # passos de tela numa tela falsa, posições, webhook
sg input -c "python3 tests/t_screen_ui.py"          # editor de passo, Escolher na tela, arranjo das telas
sg input -c "python3 tests/t_abs_pointer_libinput.py"  # o ponteiro absoluto como o libinput lê
sg input -c "python3 tests/t_farm_macros.py"        # vigias, janelas, contadores, o gravador, um farm inteiro num jogo falso
sg input -c "python3 tests/t_farm_ui.py"            # editores, gravador, assistente de farm, arquivos .wayclick
python3 tests/t_dock_ui.py                          # a aba Doca, macros da Doca, editores e links numa Doca falsa
python3 tests/t_tower_ui.py                         # a aba Tower Defense: estratégias, gravador, arquivos e um farm inteiro num jogo falso
python3 tests/t_farm_kwin.py                        # passos de janela e o cursor contra um KWin de verdade, aninhado
python3 tests/t_dock_kwin.py                        # a Doca num KWin de verdade, aninhado: teclas, cliques, roda, tela, janelas, tamanho, links, fechar
python3 tests/t_dock_hidden.py                      # um jogo continua desenhando na Doca atrás de outras janelas e minimizada
python3 tests/t_tower_kwin.py                       # o farm e a visão da aba Tower Defense num jogo falso na Doca de verdade
python3 tests/t_sober_dock.py                       # o Sober abrindo na Doca, com o botão Close achado e clicado
python3 tests/t_screen_portal.py                    # o portal de verdade, dentro do bubblewrap
python3 tests/bench_find_image.py                   # busca de imagem, acha ou não e com que rapidez
python3 tests/t_sober_kwin.py                       # tudo isso contra o Roblox (Sober), num KWin aninhado
python3 tests/t_sidenav.py                          # a barra lateral: grupos, pontos, busca, teclas, só ícones quando estreita
python3 tests/t_sober_accounts.py                   # as Contas do Sober num Sober falso: trava, pasta, fechar só a cópia dela, a página, a Doca
python3 tests/t_sober_accounts_kwin.py              # duas contas com o Sober de verdade ao mesmo tempo, num KWin aninhado
```

O `t_screen_portal.py` roda o `xdg-desktop-portal` de verdade dentro do `bwrap`,
com D-Bus próprio, PipeWire próprio e um backend ScreenCast falso que serve
telas sintéticas, então nenhum diálogo aparece e nada da sua tela é
compartilhado. Ele precisa de `bwrap`, `dbus-run-session`, `pipewire` e
`wireplumber`, e pula quando falta algum.

O `t_sober_kwin.py` roda o WayClick contra a janela de um jogo de verdade. O
`tests/kwin_nested.py` sobe um `kwin_wayland --virtual` aninhado, com duas
telas, dentro do `bwrap`, com D-Bus próprio, PipeWire próprio com saída muda, o
`xdg-desktop-portal-kde` de verdade (o teste responde o diálogo de
compartilhar clicando nele) e uma ponte de entrada que entrega cada device
virtual do WayClick só ao KWin aninhado. Um Sober novo abre ali, com HOME
próprio em `/var/tmp` (do seu vêm só os APKs do Roblox e a versão, nada de
conta), rede própria pelo `pasta` (pacote `passt`) e a menor prioridade de CPU
e disco, então o seu Sober pode ficar aberto. O teclado falso só digita
enquanto o WayClick de dentro da caixa diz que o segura e a captura dá
ocupada. O teste importa scripts de `tests/ahk` e confere no formulário de
login do Roblox, lendo o texto de volta pela área de transferência da caixa;
roda os passos de tela na tela inicial do Roblox (imagem achada e clicada, cor
de pixel, notificações, prints no webhook, abrir link); e passa pelo token de
restauração e pelas escolhas do diálogo. Precisa do Sober com o Roblox já
baixado uma vez, `pasta`, `bwrap`, `kwin_wayland`, `wl-clipboard` e
`kscreen-doctor`, e pula quando falta algum (`WCTEST_PASTA=/caminho/do/pasta`
usa um que não esteja instalado).

O `t_ahk_kwin.py` usa a mesma caixa com uma tela só: uma janela alvo que
mostra as teclas e os cliques que recebe, e o WayClick de verdade importando o
`tests/ahk/kwin_e2e.ahk`. Segurar Espaço manda F9 umas 32 vezes por segundo
(mediana medida de 31,3 ms), F7 suspende e volta, e os atalhos de `ImageSearch`
e `PixelSearch` clicam na imagem e no ponto vermelho. A tela vem do
ScreenShot2 do KWin da caixa, e duas capturas ficam em `/tmp/wayclick-ahk-kwin`
(ou em `WCTEST_SHOTS`).

O `t_dock_kwin.py`, o `t_dock_hidden.py`, o `t_tower_kwin.py` e o `t_sober_dock.py` abrem a Doca
dentro da mesma caixa, então a janela dela cai no KWin aninhado e nada chega
ao seu desktop. O `t_sober_dock.py` deixa a Doca abrir o Sober com o comando
dela. A caixa não tem rede, então esse Sober nunca encontra o seu pela porta
fixa do 127.0.0.1 e não vê conta nenhuma (só os APKs do Roblox e a versão
são copiados, como acima), e ele para na tela "Failed to fetch remote
manifest" dele. Um macro da Doca então acha o botão Close dessa tela pela
imagem e clica, e o Sober fecha. O Roblox em si precisa de rede, então essa
parte fica com o `t_sober_kwin.py`. O `t_dock_hidden.py` precisa do
`es2gears_wayland` (mesa-demos) e pula sem ele.

O `t_sober_accounts.py` roda o `flock`, o `pasta` e o `unshare` de verdade em
volta de um Sober falso que segura a porta do 127.0.0.1 em cada rede, com
`flatpak`, `systemctl` e `systemd-run` falsos na frente do PATH. Ele se recusa
a abrir na rede do seu computador, então nunca encontra o seu Sober. O
`t_sober_accounts_kwin.py` abre três contas com o Sober de verdade no KWin
aninhado, cada uma pelo `pasta` com internet e sem login (os APKs do Roblox e
a versão são copiados do seu, como acima), na menor prioridade de CPU, e
confere antes de fechar qualquer coisa que ela é do teste. Sem o `pasta` o
primeiro pula as partes que abrem um Sober, e o segundo pula.

Rode com `QT_QPA_PLATFORM=offscreen`. Os outros precisam do compositor; rode a
partir da raiz do projeto:

```bash
sg input -c "python3 tests/t_hold.py"        # modo segurar de ponta a ponta, com mouse falso
sg input -c "python3 tests/t_hold_keyonly.py"  # modo segurar acionando só a macro de teclado
sg input -c "python3 tests/t_trigger_scope.py" # teclado solto enquanto o clique espera o botão
sg input -c "python3 tests/t_macro_trigger.py" # segurar botão do mouse, e segurar tecla, acionando a macro
python3 tests/t_multikey.py                  # várias teclas ao mesmo tempo, cada uma no seu intervalo
python3 tests/t_update.py                    # aviso de versão nova: compara, guarda e mostra
sg input -c "python3 tests/t_clone.py"       # pergunta ao KWin via D-Bus se o clone herdou sua config
sg input -c "python3 tests/t_hotkey.py"      # atalho global, com teclado falso
sg input -c "python3 tests/t_relay_lat.py"   # latência do relay sob carga de 10 kHz
sg input -c "python3 tests/t_multimouse.py"  # vários mouses, cada um clonado com sua identidade
python3 tests/t_fast.py 0.1                  # cliques emitidos x entregues a 10.000/s
python3 tests/t_keymacro.py                  # teclas entregues, e nunca deixadas presas
python3 tests/t_antiafk.py                   # amplitude do nudge na tela e deriva ao longo do tempo
sg input -c "python3 tests/t_xinject.py"     # tecla numa janela X11, sem foco e depois minimizada
sg input -c "python3 tests/t_target.py"      # o mesmo para alvo Wayland, pelo caminho do foco
sg input -c "python3 tests/t_holdtime.py"    # duração da tecla, medida como um jogo consulta
```

Eles abrem uma janela em tela cheia para contar o que realmente chega, então a
tela pisca por alguns segundos. Cada teste usa um config descartável próprio,
nenhum deles encosta nas suas configurações.

## Traduzindo

As traduções são um dicionário simples dentro do `wayclick.py` — sem arquivos
`.ts`, sem etapa de build, porque o app é um arquivo só. Para adicionar um
idioma, copie o bloco `"pt_BR"` em `TRANSLATIONS`, troque a chave pelo prefixo
do seu locale, traduza o lado direito e acrescente em `LANGS`. O que faltar cai
no inglês, então tradução parcial já funciona. Textos com `{nome}` mantêm os
marcadores.

## Observações

As configurações ficam em `~/.config/wayclick.json`. Nada roda como root, nada é
instalado no sistema além da regra de udev, e os devices virtuais somem quando o
processo termina. As imagens dos passos de imagem ficam em
`~/.local/share/wayclick/images`, e o token de compartilhamento de tela em
`~/.config/wayclick/screen-token`. A tela só é compartilhada enquanto um macro
precisa dela, e nenhum quadro sai da máquina, a não ser que um passo poste um
print no webhook que você deu a ele. As imagens de arquivos `.wayclick`
importados vão para a mesma pasta de imagens. As Contas do Sober ficam em
`~/.local/share/wayclick/sober-accounts`, uma pasta para cada.

Use onde automação for permitida. Muito jogo online bane automação de entrada.

## Construindo os pacotes

```bash
bash packaging/build-rpm.sh        # precisa de rpm-build
bash packaging/build-deb.sh        # só precisa de ar e tar, sem dpkg
bash packaging/build-pacman.sh     # precisa de bsdtar, constrói fora do Arch
bash packaging/build-appimage.sh   # baixa um Python e um Qt portáteis
makepkg -p packaging/PKGBUILD      # a via a partir do fonte, no Arch
```

Tudo cai em `dist/`. Um push de tag constrói os quatro no CI e anexa na
release — veja [`.github/workflows/release.yml`](.github/workflows/release.yml).

## Desinstalando

```bash
bash uninstall.sh            # mantém suas configurações, a regra de udev e as Contas do Sober
bash uninstall.sh --purge    # remove essas também, menos uma Conta do Sober que esteja aberta
```

Se instalou por pacote, use o gerenciador (`dnf remove wayclick`,
`apt remove wayclick`, `pacman -R wayclick`).

## Licença

MIT — veja [LICENSE](LICENSE).
