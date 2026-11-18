#Requires AutoHotkey v2
*XButton2::
{
    while GetKeyState("XButton2", "P")
    {
        Send "{LButton}"
        Sleep Random(40, 60)
    }
}
F1::SetTimer(() => Send("f"), -500)
