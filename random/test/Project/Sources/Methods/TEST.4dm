//%attributes = {}
$status:=generate random number 

$data:=generate random bytes (New object:C1471("size";10))


  //default alg=SecRandomCopyBytes(mac)
  // see https://forums.developer.apple.com/thread/96907

  //default alg=BCryptGenRandom(win)