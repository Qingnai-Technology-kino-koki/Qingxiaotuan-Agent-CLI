#include <stdio.h>
#include <string.h>
#include <stdint.h>
#define main crypto_main
#include "crypto/crypto.c"
#undef main
int main(void){
    uint8_t K[32]={0xfe,0xff,0xe9,0x92,0x86,0x65,0x73,0x1c,0x6d,0x6a,0x8f,0x94,0x67,0x30,0x83,0x08,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0};
    uint32_t rk[60]; aes_keyexp(K,rk);
    uint8_t H[16]={0}; aes_encrypt_block(H,H,rk);
    printf("H  = "); for(int i=0;i<16;i++) printf("%02x", H[i]); printf(" (NIST exp 52426a1cbf1566a297f6b1c3297222cd)\n");
    return 0;
}
