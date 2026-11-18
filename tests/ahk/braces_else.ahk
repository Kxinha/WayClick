F1::
    toggle := !toggle
    if (toggle) {
        SetTimer, Spam, 25
    } else {
        SetTimer, Spam, Off
    }
return
Spam:
    Send, {e 2}
    Click 100, 200
return
