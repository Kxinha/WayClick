SendMode Event
SetKeyDelay, 10, 30
F10::
Toggle := !Toggle
Loop {
    If (!Toggle)
        Break
    Send, 1
    Random, r, 80, 120
    Sleep, %r%
}
Return
