#include <stdio.h>
#include <string.h>
#include <stdint.h>
#define main crypto_main
#include "crypto/crypto.c"
#undef main
int main(void){
    uint8_t K[32]; for(int i=0;i<32;i++) K[i]=(uint8_t)i;
    uint8_t IV[12]; for(int i=0;i<12;i++) IV[i]=(uint8_t)(i*7+3);
    uint8_t P[60]; for(int i=0;i<60;i++) P[i]=(uint8_t)(i*3+1);
    uint8_t AAD[20]; for(int i=0;i<20;i++) AAD[i]=(uint8_t)(i*5);
    uint8_t ct[60], tag[16], pt2[60];
    aes_gcm_encrypt(K,IV,P,60,AAD,20,ct,tag);
    /* 篡改检测: 改一个密文位 */
    uint8_t ctbad[60]; memcpy(ctbad,ct,60); ctbad[10]^=0xff;
    int ok_tamper = aes_gcm_decrypt(K,IV,ctbad,60,AAD,20,tag,pt2);
    int ok_dec = aes_gcm_decrypt(K,IV,ct,60,AAD,20,tag,pt2);
    int ok_match = (ok_dec && memcmp(pt2,P,60)==0);
    /* 错误 AAD 也应失败 */
    uint8_t AAD2[20]; memcpy(AAD2,AAD,20); AAD2[0]^=1;
    int ok_aad = aes_gcm_decrypt(K,IV,ct,60,AAD2,20,tag,pt2);
    printf("GCM roundtrip: %s\n", ok_match?"PASS":"FAIL");
    printf("GCM tamper detected: %s\n", ok_tamper?"FAIL(no detect)":"PASS(detected)");
    printf("GCM wrong-aad rejected: %s\n", ok_aad?"FAIL(no detect)":"PASS(detected)");
    return (ok_match && !ok_tamper && !ok_aad)?0:1;
}
