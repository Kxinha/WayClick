F3::
toggle := !toggle
while (toggle) {
    Send, {w down}
    Sleep, 100
    Send, {w up}
    Sleep, 50
}
return
