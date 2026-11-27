; Segurar Espaço aperta F9 sem parar (o pulo do jogo no F9), F7 liga e
; desliga tudo. Feito para o WayClick, no estilo de um bhop do AutoHotkey.
#NoEnv
SendMode Input

F7::Suspend

*$Space::
while GetKeyState("Space", "P") {
    Send {Blind}{F9}
    Sleep 15
}
return
