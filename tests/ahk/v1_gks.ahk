F6::
Loop
{
    GetKeyState, state, F6, P
    if state = U
        break
    Send, r
    Sleep, 5
}
return
