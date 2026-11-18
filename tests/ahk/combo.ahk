; comentario
F5::
Loop, 3
{
    Click, right
    Sleep, 100
}
Send {Shift down}a{Shift up}
MouseMove, 10, 0, 0, R
return
*F6::Send {WheelDown 3}
F7 & F8::Send x
F9 up::Send y
:*:btw::by the way
#IfWinActive ahk_exe game.exe
Numpad1::Send {Numpad5}
