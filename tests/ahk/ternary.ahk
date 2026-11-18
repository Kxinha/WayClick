F1::SetTimer, Spam, % (t := !t) ? 10 : "Off"
Spam:
Send {Blind}{q}
return
^+F2::
Toggle := !Toggle
SetTimer, Spam, % Toggle ? 1 : "Off"
return
