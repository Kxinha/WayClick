; WayClick end to end test in a nested KWin (tests/t_ahk_kwin.py)
#NoEnv
CoordMode, Pixel, Screen
CoordMode, Mouse, Screen

F7::Suspend

*$Space::
while GetKeyState("Space", "P") {
    Send {Blind}{F9}
    Sleep 15
}
return

F6::
ImageSearch, FoundX, FoundY, 0, 0, A_ScreenWidth, A_ScreenHeight, *30 alvo.png
if (ErrorLevel = 0)
    Click, %FoundX%, %FoundY%
else
    Send n
return

F4::
PixelSearch, Px, Py, 0, 0, A_ScreenWidth, A_ScreenHeight, 0x0000FF, 10
if !ErrorLevel
    Click %Px% %Py%
return
