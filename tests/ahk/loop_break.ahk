$XButton1::
Loop
{
    if !GetKeyState("XButton1", "P")
        break
    SendInput, {e}
    Sleep 20
}
return
