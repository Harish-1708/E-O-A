// The address of the Google script that stores the opt-outs: its Web app URL (it ends in /exec).
// Copy it from Deploy > Manage deployments in the Google script editor, NEVER from your browser's address bar (that can contain /u/6/ or similar, which only means something in your own browser).
// If you ever make a NEW deployment, its address changes: update the line below and commit it. The page only talks to script.google.com addresses of this exact shape.
window.UNSUBSCRIBE_API = "https://script.google.com/macros/s/AKfycbytTNqD3rf05EYnWiuuVMzzrGP24nhxCBHs7UzBnUj3yxJwL2VZoeIwohb6lcMli_g9/exec";
