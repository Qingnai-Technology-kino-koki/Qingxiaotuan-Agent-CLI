#include <stdio.h>
#include <string.h>
#include <stdint.h>
#define main crypto_main
#include "crypto/crypto.c"
#undef main
int main(void){
    uint8_t K[32]={0}; uint8_t IV[12]={0};
    uint8_t ct[1], tag[16];
    aes_gcm_encrypt(K,IV,NULL,0,NULL,0,ct,tag);
    static const uint8_t expT[16]={0x53,0x0f,0x8a,0xfb,0xc7,0x45,0x36,0xb9,0xa9,0x63,0xb4,0xf1,0xc4,0xcb,0x73,0x8b};
    printf("zero-vec tag: "); for(int i=0;i<16;i++) printf("%02x",tag[i]); printf("\n");
    printf("zero-vec %s\n", memcmp(tag,expT,16)==0?"PASS":"FAIL");
    return 0;
}
