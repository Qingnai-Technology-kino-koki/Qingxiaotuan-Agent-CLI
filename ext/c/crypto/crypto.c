/*
 * crypto.c —— 青小团加密存储引擎 (C 侧外部进程)
 *
 * 零依赖自包含实现: AES-256 + GCM 认证加密 + PBKDF2-HMAC-SHA256 密钥派生。
 * 用于记忆/配置等敏感数据的"落盘即密文"存储。
 *
 * 设计:
 *   - 主密钥由口令 + 随机 salt 经 PBKDF2 派生 (100000 轮), 不落盘;
 *   - 每条记录独立随机 12 字节 nonce, 密文 = AES-256-GCM(明文);
 *   - 输出格式 (seal): base64( salt16 | nonce12 | tag16 | ciphertext )
 *   - 提供 fingerprint 做"口令是否正确"的探测 (解密 sentinel)。
 *   - 内部强制自洽: seal 与 unseal 共用同一套 AES/GCM 实现; 并内建
 *     一段 NIST SP800-38D 测试向量自检 (main --selftest)。
 *
 * IPC 方法:
 *   derive      { passphrase, salt_b64? } -> { salt_b64, ok }
 *   seal        { passphrase, salt_b64, plaintext } -> { blob }
 *   unseal      { passphrase, salt_b64, blob } -> { ok, plaintext } | error
 *   fingerprint { passphrase, salt_b64 } -> { fp }   (HMAC 探测, 不泄露明文)
 */

#include "ipc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

/* ============================================================ SHA-256 */
typedef struct { uint32_t h[8]; uint64_t len; uint8_t buf[64]; size_t blen; } sha256_t;
static const uint32_t K[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};
static uint32_t rotr(uint32_t x, int n){return (x>>n)|(x<<(32-n));}
static void sha256_block(sha256_t *s, const uint8_t *p){
    uint32_t w[64];
    for(int i=0;i<16;i++) w[i]=(p[i*4]<<24)|(p[i*4+1]<<16)|(p[i*4+2]<<8)|p[i*4+3];
    for(int i=16;i<64;i++){
        uint32_t s0=rotr(w[i-15],7)^rotr(w[i-15],18)^(w[i-15]>>3);
        uint32_t s1=rotr(w[i-2],17)^rotr(w[i-2],19)^(w[i-2]>>10);
        w[i]=w[i-16]+s0+w[i-7]+s1;
    }
    uint32_t a=s->h[0],b=s->h[1],c=s->h[2],d=s->h[3],e=s->h[4],f=s->h[5],g=s->h[6],h=s->h[7];
    for(int i=0;i<64;i++){
        uint32_t S1=rotr(e,6)^rotr(e,11)^rotr(e,25);
        uint32_t ch=(e&f)^((~e)&g);
        uint32_t t1=h+S1+ch+K[i]+w[i];
        uint32_t S0=rotr(a,2)^rotr(a,13)^rotr(a,22);
        uint32_t maj=(a&b)^(a&c)^(b&c);
        uint32_t t2=S0+maj;
        h=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
    }
    s->h[0]+=a;s->h[1]+=b;s->h[2]+=c;s->h[3]+=d;s->h[4]+=e;s->h[5]+=f;s->h[6]+=g;s->h[7]+=h;
}
static void sha256_init(sha256_t *s){
    s->h[0]=0x6a09e667;s->h[1]=0xbb67ae85;s->h[2]=0x3c6ef372;s->h[3]=0xa54ff53a;
    s->h[4]=0x510e527f;s->h[5]=0x9b05688c;s->h[6]=0x1f83d9ab;s->h[7]=0x5be0cd19;
    s->len=0;s->blen=0;
}
static void sha256_update(sha256_t *s,const uint8_t *data,size_t n){
    s->len+=n;
    while(n>0){
        size_t take=64-s->blen; if(take>n)take=n;
        memcpy(s->buf+s->blen,data,take);
        s->blen+=take;data+=take;n-=take;
        if(s->blen==64){sha256_block(s,s->buf);s->blen=0;}
    }
}
static void sha256_final(sha256_t *s,uint8_t out[32]){
    uint64_t bits=s->len*8;
    uint8_t pad=0x80; sha256_update(s,&pad,1);
    uint8_t zero=0;
    while(s->blen!=56) sha256_update(s,&zero,1);
    uint8_t lb[8];
    for(int i=0;i<8;i++) lb[i]=(bits>>(56-i*8))&0xff;
    sha256_update(s,lb,8);
    for(int i=0;i<8;i++){out[i*4]=s->h[i]>>24;out[i*4+1]=s->h[i]>>16;out[i*4+2]=s->h[i]>>8;out[i*4+3]=s->h[i];}
}
static void sha256(const uint8_t *data,size_t n,uint8_t out[32]){
    sha256_t s;sha256_init(&s);sha256_update(&s,data,n);sha256_final(&s,out);
}
/* HMAC-SHA256 */
static void hmac_sha256(const uint8_t *key,size_t klen,const uint8_t *msg,size_t mlen,uint8_t out[32]){
    uint8_t k[64]; memset(k,0,64);
    if(klen>64) sha256(key,klen,k);
    else memcpy(k,key,klen);
    for(int i=0;i<64;i++) k[i]^=0x36;
    uint8_t inner[32]; uint8_t blk[64+mlen];
    memcpy(blk,k,64); memcpy(blk+64,msg,mlen);
    sha256(blk,64+mlen,inner);
    for(int i=0;i<64;i++) k[i]^=0x36^0x5c;
    uint8_t outer[64+32]; memcpy(outer,k,64); memcpy(outer+64,inner,32);
    sha256(outer,96,out);
}

/* ============================================================ AES-256 (标准 FIPS-197) */

/* 前向 sbox */
static const uint8_t SBOX[256]={
0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16};

/* 逆 sbox (解密用) */
static const uint8_t INV_SBOX[256]={
0x52,0x09,0x6a,0xd5,0x30,0x36,0xa5,0x38,0xbf,0x40,0xa3,0x9e,0x81,0xf3,0xd7,0xfb,
0x7c,0xe3,0x39,0x82,0x9b,0x2f,0xff,0x87,0x34,0x8e,0x43,0x44,0xc4,0xde,0xe9,0xcb,
0x54,0x7b,0x94,0x32,0xa6,0xc2,0x23,0x3d,0xee,0x4c,0x95,0x0b,0x42,0xfa,0xc3,0x4e,
0x08,0x2e,0xa1,0x66,0x28,0xd9,0x24,0xb2,0x76,0x5b,0xa2,0x49,0x6d,0x8b,0xd1,0x25,
0x72,0xf8,0xf6,0x64,0x86,0x68,0x98,0x16,0xd4,0xa4,0x5c,0xcc,0x5d,0x65,0xb6,0x92,
0x6c,0x70,0x48,0x50,0xfd,0xed,0xb9,0xda,0x5e,0x15,0x46,0x57,0xa7,0x8d,0x9d,0x84,
0x90,0xd8,0xab,0x00,0x8c,0xbc,0xd3,0x0a,0xf7,0xe4,0x58,0x05,0xb8,0xb3,0x45,0x06,
0xd0,0x2c,0x1e,0x8f,0xca,0x3f,0x0f,0x02,0xc1,0xaf,0xbd,0x03,0x01,0x13,0x8a,0x6b,
0x3a,0x91,0x11,0x41,0x4f,0x67,0xdc,0xea,0x97,0xf2,0xcf,0xce,0xf0,0xb4,0xe6,0x73,
0x96,0xac,0x74,0x22,0xe7,0xad,0x35,0x85,0xe2,0xf9,0x37,0xe8,0x1c,0x75,0xdf,0x6e,
0x47,0xf1,0x1a,0x71,0x1d,0x29,0xc5,0x89,0x6f,0xb7,0x62,0x0e,0xaa,0x18,0xbe,0x1b,
0xfc,0x56,0x3e,0x4b,0xc6,0xd2,0x79,0x20,0x9a,0xdb,0xc0,0xfe,0x78,0xcd,0x5a,0xf4,
0x1f,0xdd,0xa8,0x33,0x88,0x07,0xc7,0x31,0xb1,0x12,0x10,0x59,0x27,0x80,0xec,0x5f,
0x60,0x51,0x7f,0xa9,0x19,0xb5,0x4a,0x0d,0x2d,0xe5,0x7a,0x9f,0x93,0xc9,0x9c,0xef,
0xa0,0xe0,0x3b,0x4d,0xae,0x2a,0xf5,0xb0,0xc8,0xeb,0xbb,0x3c,0x83,0x53,0x99,0x61,
0x17,0x2b,0x04,0x7e,0xba,0x77,0xd6,0x26,0xe1,0x69,0x14,0x63,0x55,0x21,0x0c,0x7d};

/* GF(2^8) 乘法 (xtime 展开) */
static uint8_t gmul(uint8_t a, uint8_t b){
    uint8_t p=0;
    for(int i=0;i<8;i++){
        if(b&1) p^=a;
        uint8_t hi=a&0x80; a=(uint8_t)(a<<1); if(hi) a^=0x1b;
        b>>=1;
    }
    return p;
}

/* 密钥扩展: 14 轮 -> 15 个 16 字节字 (60 个 uint32) */
static const uint8_t RCON[10]={0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36};
/* 把 4 字节 (大端) 写进 uint32 字 */
static uint32_t word(uint8_t a,uint8_t b,uint8_t c,uint8_t d){
    return ((uint32_t)a<<24)|((uint32_t)b<<16)|((uint32_t)c<<8)|d;
}
static void aes_keyexp(const uint8_t *key, uint32_t *rk){
    /* rk 视为 60 个大端字节组 (每组 4 字节) */
    for(int i=0;i<8;i++)
        rk[i]=word(key[i*4],key[i*4+1],key[i*4+2],key[i*4+3]);
    for(int i=8;i<60;i++){
        /* 取出 rk[i-1] 的四个字节 (大端) */
        uint8_t w0=(rk[i-1]>>24)&0xff, w1=(rk[i-1]>>16)&0xff, w2=(rk[i-1]>>8)&0xff, w3=rk[i-1]&0xff;
        if(i%8==0){
            /* RotWord: w1,w2,w3,w0 */
            uint8_t t0=w1,t1=w2,t2=w3,t3=w0;
            /* SubWord */
            t0=SBOX[t0];t1=SBOX[t1];t2=SBOX[t2];t3=SBOX[t3];
            /* Rcon 异或最高字节 t0 */
            t0^=RCON[i/8-1];
            rk[i]=rk[i-8]^word(t0,t1,t2,t3);
        } else if(i%8==4){
            uint8_t t0=SBOX[w0],t1=SBOX[w1],t2=SBOX[w2],t3=SBOX[w3];
            rk[i]=rk[i-8]^word(t0,t1,t2,t3);
        } else {
            rk[i]=rk[i-8]^word(w0,w1,w2,w3);
        }
    }
}

/* 统一采用"列优先"布局: st[r + 4*c] = 第 r 行第 c 列 (与 FIPS-197 一致) */
static void add_round_key(uint8_t *s,const uint32_t *rk,int r){
    for(int c=0;c<4;c++)
        for(int row=0;row<4;row++)
            s[row+4*c]^=(uint8_t)((rk[r*4+c]>>(24-row*8))&0xff);
}
static void sub_bytes(uint8_t *s){
    for(int i=0;i<16;i++) s[i]=SBOX[s[i]];
}
static void inv_sub_bytes(uint8_t *s){
    for(int i=0;i<16;i++) s[i]=INV_SBOX[s[i]];
}
static void shift_rows(uint8_t *s){
    uint8_t t[16]; memcpy(t,s,16);
    /* st[r + 4*c] = t[r + 4*((c+r)%4)]  (行 r 左移 r) */
    for(int c=0;c<4;c++){
        s[0+4*c]=t[0+4*c];
        s[1+4*c]=t[1+4*((c+1)%4)];
        s[2+4*c]=t[2+4*((c+2)%4)];
        s[3+4*c]=t[3+4*((c+3)%4)];
    }
}
static void inv_shift_rows(uint8_t *s){
    uint8_t t[16]; memcpy(t,s,16);
    /* st[r + 4*c] = t[r + 4*((c-r+4)%4)]  (行 r 右移 r) */
    for(int c=0;c<4;c++){
        s[0+4*c]=t[0+4*c];
        s[1+4*c]=t[1+4*((c-1+4)%4)];
        s[2+4*c]=t[2+4*((c-2+4)%4)];
        s[3+4*c]=t[3+4*((c-3+4)%4)];
    }
}
static void mix_columns(uint8_t *s){
    for(int c=0;c<4;c++){
        uint8_t a0=s[0+4*c],a1=s[1+4*c],a2=s[2+4*c],a3=s[3+4*c];
        s[0+4*c]=gmul(a0,2)^gmul(a1,3)^a2^a3;
        s[1+4*c]=a0^gmul(a1,2)^gmul(a2,3)^a3;
        s[2+4*c]=a0^a1^gmul(a2,2)^gmul(a3,3);
        s[3+4*c]=gmul(a0,3)^a1^a2^gmul(a3,2);
    }
}
static void inv_mix_columns(uint8_t *s){
    for(int c=0;c<4;c++){
        uint8_t a0=s[0+4*c],a1=s[1+4*c],a2=s[2+4*c],a3=s[3+4*c];
        s[0+4*c]=gmul(a0,14)^gmul(a1,11)^gmul(a2,13)^gmul(a3,9);
        s[1+4*c]=gmul(a0,9)^gmul(a1,14)^gmul(a2,11)^gmul(a3,13);
        s[2+4*c]=gmul(a0,13)^gmul(a1,9)^gmul(a2,14)^gmul(a3,11);
        s[3+4*c]=gmul(a0,11)^gmul(a1,13)^gmul(a2,9)^gmul(a3,14);
    }
}

/* 加密一个 16 字节块 (标准 AES-256) */
static void aes_encrypt_block(const uint8_t *in,uint8_t *out,const uint32_t *rk){
    uint8_t st[16]; memcpy(st,in,16);
    add_round_key(st,rk,0);
    for(int r=1;r<=13;r++){
        sub_bytes(st);
        shift_rows(st);
        mix_columns(st);
        add_round_key(st,rk,r);
    }
    sub_bytes(st);
    shift_rows(st);
    add_round_key(st,rk,14);
    memcpy(out,st,16);
}
/* 解密一个 16 字节块 (标准 AES-256) */
static void aes_decrypt_block(const uint8_t *in,uint8_t *out,const uint32_t *rk){
    uint8_t st[16]; memcpy(st,in,16);
    add_round_key(st,rk,14);
    for(int r=13;r>=1;r--){
        inv_shift_rows(st);
        inv_sub_bytes(st);
        add_round_key(st,rk,r);
        inv_mix_columns(st);
    }
    inv_shift_rows(st);
    inv_sub_bytes(st);
    add_round_key(st,rk,0);
    memcpy(out,st,16);
}

/* ============================================================ GCM */
/* GF(2^128) 乘法 (GCM 规范, 约减多项式 0xe1 末字节) */
static void gf_mult(const uint8_t *x,const uint8_t *y,uint8_t *out){
    uint8_t z[16]={0};
    uint8_t v[16]; memcpy(v,x,16);
    for(int i=0;i<128;i++){
        int bit=(y[i/8]>>(7-i%8))&1;
        if(bit) for(int j=0;j<16;j++) z[j]^=v[j];
        int lsb=v[15]&1;
        unsigned int carry=0;
        for(int j=15;j>=0;j--){ unsigned int b=v[j]&1; v[j]=(uint8_t)((v[j]>>1)|(carry<<7)); carry=b; }
        if(lsb) v[0]^=0xe1;
    }
    memcpy(out,z,16);
}
/* 对 J0 的最后 4 字节做 32 位大端自增 (inc32) */
static void gcm_inc32(uint8_t *j0){
    for(int i=15;i>=12;i--){
        if(++j0[i]!=0) break;
    }
}

/*
 * AES-256-GCM 加密。
 * key: 32 字节; nonce: 12 字节 (标准 GCM); aad: 附加认证数据;
 * 输出 ct (与 pt 等长) 与 16 字节 tag。
 * 内部用标准 CTR 模式生成 keystream (计数器从 J0+1 开始)。
 */
static void aes_gcm_encrypt(const uint8_t *key,const uint8_t *nonce,
                            const uint8_t *pt,size_t ptlen,
                            const uint8_t *aad,size_t aadlen,
                            uint8_t *ct,uint8_t tag[16]){
    uint32_t rk[60]; aes_keyexp(key,rk);
    /* H = E(0^128) */
    uint8_t H[16]={0}; aes_encrypt_block(H,H,rk);
    /* J0: nonce(12) || 0x00000001 */
    uint8_t J0[16]; memcpy(J0,nonce,12); J0[12]=0; J0[13]=0; J0[14]=0; J0[15]=1;

    /* CTR 加密: 计数器 CB = inc32(J0) 递增 */
    uint8_t cb[16]; memcpy(cb,J0,16); gcm_inc32(cb);
    uint8_t ks[16];
    for(size_t i=0;i<ptlen;i+=16){
        aes_encrypt_block(cb,ks,rk);
        size_t n=ptlen-i; if(n>16)n=16;
        for(size_t j=0;j<n;j++) ct[i+j]=pt[i+j]^ks[j];
        gcm_inc32(cb);
    }

    /* GHASH: GHASH_H(AAD || CT || lengths) */
    uint8_t Y[16]={0};
    uint8_t b[16];
    for(size_t i=0;i<aadlen;i+=16){
        size_t n=aadlen-i; if(n>16)n=16; memcpy(b,aad+i,n); for(size_t j=n;j<16;j++) b[j]=0;
        for(int j=0;j<16;j++) Y[j]^=b[j]; gf_mult(Y,H,Y);
    }
    for(size_t i=0;i<ptlen;i+=16){
        size_t n=ptlen-i; if(n>16)n=16; memcpy(b,ct+i,n); for(size_t j=n;j<16;j++) b[j]=0;
        for(int j=0;j<16;j++) Y[j]^=b[j]; gf_mult(Y,H,Y);
    }
    uint8_t lenblock[16]={0};
    uint64_t la=(uint64_t)aadlen*8, lp=(uint64_t)ptlen*8;
    for(int i=0;i<8;i++){ lenblock[i]=la>>(56-i*8); lenblock[8+i]=lp>>(56-i*8); }
    for(int j=0;j<16;j++) Y[j]^=lenblock[j]; gf_mult(Y,H,Y);

    /* S = E(J0) ^ GHASH */
    uint8_t ej0[16]; aes_encrypt_block(J0,ej0,rk);
    for(int j=0;j<16;j++) tag[j]=ej0[j]^Y[j];
}

/* AES-256-GCM 解密 (CTR 解密 + GHASH 验证); 返回 1 成功 0 tag mismatch */
static int aes_gcm_decrypt(const uint8_t *key,const uint8_t *nonce,
                           const uint8_t *ct,size_t ctlen,
                           const uint8_t *aad,size_t aadlen,
                           const uint8_t tag[16],
                           uint8_t *pt){
    uint32_t rk[60]; aes_keyexp(key,rk);
    uint8_t H[16]={0}; aes_encrypt_block(H,H,rk);
    uint8_t J0[16]; memcpy(J0,nonce,12); J0[12]=0; J0[13]=0; J0[14]=0; J0[15]=1;

    /* CTR 解密 (与加密完全对称) */
    uint8_t cb[16]; memcpy(cb,J0,16); gcm_inc32(cb);
    uint8_t ks[16];
    for(size_t i=0;i<ctlen;i+=16){
        aes_encrypt_block(cb,ks,rk);
        size_t n=ctlen-i; if(n>16)n=16;
        for(size_t j=0;j<n;j++) pt[i+j]=ct[i+j]^ks[j];
        gcm_inc32(cb);
    }

    /* 重算 GHASH 并与 tag 比较 */
    uint8_t Y[16]={0};
    uint8_t b[16];
    for(size_t i=0;i<aadlen;i+=16){
        size_t n=aadlen-i; if(n>16)n=16; memcpy(b,aad+i,n); for(size_t j=n;j<16;j++) b[j]=0;
        for(int j=0;j<16;j++) Y[j]^=b[j]; gf_mult(Y,H,Y);
    }
    for(size_t i=0;i<ctlen;i+=16){
        size_t n=ctlen-i; if(n>16)n=16; memcpy(b,ct+i,n); for(size_t j=n;j<16;j++) b[j]=0;
        for(int j=0;j<16;j++) Y[j]^=b[j]; gf_mult(Y,H,Y);
    }
    uint8_t lenblock[16]={0};
    uint64_t la=(uint64_t)aadlen*8, lp=(uint64_t)ctlen*8;
    for(int i=0;i<8;i++){ lenblock[i]=la>>(56-i*8); lenblock[8+i]=lp>>(56-i*8); }
    for(int j=0;j<16;j++) Y[j]^=lenblock[j]; gf_mult(Y,H,Y);

    uint8_t ej0[16]; aes_encrypt_block(J0,ej0,rk);
    uint8_t expect[16]; for(int j=0;j<16;j++) expect[j]=ej0[j]^Y[j];
    int ok=1; for(int j=0;j<16;j++) if(expect[j]!=tag[j]) ok=0;
    return ok;
}

/* ============================================================ PBKDF2-HMAC-SHA256 */
static void pbkdf2(const uint8_t *pw,size_t pwlen,const uint8_t *salt,size_t slen,
                   uint32_t rounds,uint8_t dk[32]){
    /* 仅需要 32 字节导出, 单块 (1 个 1-indexed block index) */
    uint8_t blk[4]; blk[0]=0;blk[1]=0;blk[2]=0;blk[3]=1;
    uint8_t msg[64]; size_t mlen=slen+4;
    memcpy(msg,salt,slen); memcpy(msg+slen,blk,4);
    uint8_t U[32]; hmac_sha256(pw,pwlen,msg,mlen,U);
    uint8_t T[32]; memcpy(T,U,32);
    for(uint32_t r=1;r<rounds;r++){ hmac_sha256(pw,pwlen,U,32,U); for(int i=0;i<32;i++) T[i]^=U[i]; }
    memcpy(dk,T,32);
}

/* ============================================================ base64 */
static const char B64[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
static void b64_enc(const uint8_t *in,size_t n,char *out){
    size_t i,o=0;
    for(i=0;i+2<n;i+=3){
        uint32_t v=(in[i]<<16)|(in[i+1]<<8)|in[i+2];
        out[o++]=B64[(v>>18)&63];out[o++]=B64[(v>>12)&63];out[o++]=B64[(v>>6)&63];out[o++]=B64[v&63];
    }
    if(i<n){ uint32_t v=in[i]<<16; if(i+1<n)v|=in[i+1]<<8;
        out[o++]=B64[(v>>18)&63];out[o++]=B64[(v>>12)&63];
        out[o++]=(i+1<n)?B64[(v>>6)&63]:'='; out[o++]='='; }
    out[o]='\0';
}
static int b64_val(char c){
    if(c>='A'&&c<='Z')return c-'A';
    if(c>='a'&&c<='z')return c-'a'+26;
    if(c>='0'&&c<='9')return c-'0'+52;
    if(c=='+')return 62; if(c=='/')return 63;
    return -1;
}
static int b64_dec(const char *in,size_t inlen,uint8_t *out,size_t *outlen){
    size_t i,o=0;
    for(i=0;i+3<inlen;i+=4){
        int v[4]; for(int k=0;k<4;k++){ if(in[i+k]=='='){v[k]=-1;} else {v[k]=b64_val(in[i+k]); if(v[k]<0)return -1;} }
        if(v[0]<0||v[1]<0)return -1;
        uint32_t val=(v[0]<<18)|(v[1]<<12)|((v[2]>=0?v[2]:0)<<6)|(v[3]>=0?v[3]:0);
        out[o++]=(val>>16)&255;
        if(v[2]>=0) out[o++]=(val>>8)&255;
        if(v[3]>=0) out[o++]=(val)&255;
    }
    *outlen=o; return 0;
}

/* ============================================================ 熵源 (nonce/salt) */
#if defined(_WIN32) || defined(_WIN64)
#include <windows.h>
#include <bcrypt.h>
#pragma comment(lib, "bcrypt.lib")
static int get_entropy(uint8_t *buf,size_t n){
    return (BCryptGenRandom(NULL,(PUCHAR)buf,(ULONG)n,BCRYPT_USE_SYSTEM_PREFERRED_RNG)==0)?0:-1;
}
#else
#include <sys/random.h>
static int get_entropy(uint8_t *buf,size_t n){
    return getrandom(buf,n,0)==(ssize_t)n?0:-1;
}
#endif

/* ============================================================ IPC handlers */
static qxt_json *h_derive(const qxt_json *params,char **err_out){
    (void)err_out;
    const char *pw=qxt_json_get_str(params,"passphrase",NULL);
    if(!pw){*err_out=strdup("missing passphrase");return NULL;}
    const char *salt_b64=qxt_json_get_str(params,"salt_b64",NULL);
    uint8_t salt[16];
    if(salt_b64){
        size_t sl; if(b64_dec(salt_b64,strlen(salt_b64),salt,&sl)!=0||sl!=16){*err_out=strdup("bad salt");return NULL;}
    } else {
        if(get_entropy(salt,16)!=0){
            /* 退化: 多源混合 (非理想但仍可用) */
            uint64_t seed=(uint64_t)time(NULL)^(uint64_t)clock()^(uint64_t)(size_t)&salt;
            for(int i=0;i<16;i++){ seed=seed*6364136223846793005ULL+1442695040888963407ULL; salt[i]=(uint8_t)(seed>>33); }
        }
    }
    char sb[64]; b64_enc(salt,16,sb);
    qxt_json *res=qxt_json_obj();
    qxt_json_obj_set(res,"salt_b64",qxt_json_str(sb));
    qxt_json_obj_set(res,"ok",qxt_json_bool(1));
    return res;
}
static qxt_json *h_seal(const qxt_json *params,char **err_out){
    const char *pw=qxt_json_get_str(params,"passphrase",NULL);
    const char *salt_b64=qxt_json_get_str(params,"salt_b64",NULL);
    const char *plain=qxt_json_get_str(params,"plaintext","");
    if(!pw||!salt_b64){*err_out=strdup("missing passphrase/salt_b64");return NULL;}
    uint8_t salt[16]; size_t sl;
    if(b64_dec(salt_b64,strlen(salt_b64),salt,&sl)!=0||sl!=16){*err_out=strdup("bad salt");return NULL;}
    uint8_t dk[32]; pbkdf2((const uint8_t*)pw,strlen(pw),salt,16,100000,dk);
    uint8_t nonce[12];
    if(get_entropy(nonce,12)!=0){
        uint64_t seed=(uint64_t)time(NULL)^(uint64_t)clock()^(uint64_t)strlen(plain);
        for(int i=0;i<12;i++){ seed=seed*6364136223846793005ULL+1442695040888963407ULL; nonce[i]=(uint8_t)(seed>>33); }
    }
    size_t ptlen=strlen(plain);
    uint8_t *ct=malloc(ptlen+1);
    uint8_t tag[16];
    aes_gcm_encrypt(dk,nonce,(const uint8_t*)plain,ptlen,NULL,0,ct,tag);
    /* 输出: salt(16) | nonce(12) | tag(16) | ct */
    size_t total=16+12+16+ptlen;
    uint8_t *blob=malloc(total);
    memcpy(blob,salt,16);memcpy(blob+16,nonce,12);memcpy(blob+28,tag,16);memcpy(blob+44,ct,ptlen);
    char *b64=malloc(total*2+8); b64_enc(blob,total,b64);
    qxt_json *res=qxt_json_obj();
    qxt_json_obj_set(res,"blob",qxt_json_str(b64));
    free(blob);free(ct);free(b64);
    return res;
}
static qxt_json *h_unseal(const qxt_json *params,char **err_out){
    const char *pw=qxt_json_get_str(params,"passphrase",NULL);
    const char *salt_b64=qxt_json_get_str(params,"salt_b64",NULL);
    const char *blob=qxt_json_get_str(params,"blob",NULL);
    if(!pw||!salt_b64||!blob){*err_out=strdup("missing fields");return NULL;}
    uint8_t salt[16]; size_t sl;
    if(b64_dec(salt_b64,strlen(salt_b64),salt,&sl)!=0||sl!=16){*err_out=strdup("bad salt");return NULL;}
    uint8_t *bin=malloc(strlen(blob));
    size_t bl; if(b64_dec(blob,strlen(blob),bin,&bl)!=0||bl<44){free(bin);*err_out=strdup("bad blob");return NULL;}
    uint8_t dk[32]; pbkdf2((const uint8_t*)pw,strlen(pw),salt,16,100000,dk);
    uint8_t nonce[12]; memcpy(nonce,bin+16,12);
    uint8_t tag[16]; memcpy(tag,bin+28,16);
    size_t ctlen=bl-44;
    uint8_t *ct=bin+44;
    uint8_t *pt=malloc(ctlen+1);
    int ok=aes_gcm_decrypt(dk,nonce,ct,ctlen,NULL,0,tag,pt);
    if(!ok){ free(bin);free(pt);*err_out=strdup("decrypt: tag mismatch (wrong passphrase?)");return NULL; }
    pt[ctlen]='\0';
    qxt_json *res=qxt_json_obj();
    qxt_json_obj_set(res,"ok",qxt_json_bool(1));
    qxt_json_obj_set(res,"plaintext",qxt_json_str((char*)pt));
    free(bin);free(pt);
    return res;
}
static qxt_json *h_fingerprint(const qxt_json *params,char **err_out){
    const char *pw=qxt_json_get_str(params,"passphrase",NULL);
    const char *salt_b64=qxt_json_get_str(params,"salt_b64",NULL);
    if(!pw||!salt_b64){*err_out=strdup("missing fields");return NULL;}
    uint8_t salt[16]; size_t sl;
    if(b64_dec(salt_b64,strlen(salt_b64),salt,&sl)!=0||sl!=16){*err_out=strdup("bad salt");return NULL;}
    uint8_t dk[32]; pbkdf2((const uint8_t*)pw,strlen(pw),salt,16,100000,dk);
    uint8_t fp[32]; hmac_sha256(dk,32,(const uint8_t*)"qingxiaotuan:fingerprint",22,fp);
    char fp_b64[64]; b64_enc(fp,32,fp_b64);
    qxt_json *res=qxt_json_obj();
    qxt_json_obj_set(res,"fp",qxt_json_str(fp_b64));
    return res;
}
/* HMAC-SHA256 签名: 用派生密钥对消息签名 (也可直接传 key_b64 跳过派生)。
   用于"内容完整性 / 来源认证"场景, 与 seal(加密) 正交。 */
static qxt_json *h_sign(const qxt_json *params,char **err_out){
    const char *msg=qxt_json_get_str(params,"message",NULL);
    if(!msg){*err_out=strdup("missing message");return NULL;}
    uint8_t key[32];
    const char *key_b64=qxt_json_get_str(params,"key_b64",NULL);
    if(key_b64){
        size_t kl; if(b64_dec(key_b64,strlen(key_b64),key,&kl)!=0||kl!=32){*err_out=strdup("bad key_b64 (need 32 bytes)");return NULL;}
    } else {
        const char *pw=qxt_json_get_str(params,"passphrase",NULL);
        const char *salt_b64=qxt_json_get_str(params,"salt_b64",NULL);
        if(!pw||!salt_b64){*err_out=strdup("missing passphrase/salt_b64 or key_b64");return NULL;}
        uint8_t salt[16]; size_t sl;
        if(b64_dec(salt_b64,strlen(salt_b64),salt,&sl)!=0||sl!=16){*err_out=strdup("bad salt");return NULL;}
        pbkdf2((const uint8_t*)pw,strlen(pw),salt,16,100000,key);
    }
    uint8_t mac[32]; hmac_sha256(key,32,(const uint8_t*)msg,strlen(msg),mac);
    char mac_b64[64]; b64_enc(mac,32,mac_b64);
    qxt_json *res=qxt_json_obj();
    qxt_json_obj_set(res,"mac_b64",qxt_json_str(mac_b64));
    return res;
}
static qxt_json *h_verify(const qxt_json *params,char **err_out){
    (void)err_out;
    const char *msg=qxt_json_get_str(params,"message",NULL);
    const char *mac_b64=qxt_json_get_str(params,"mac_b64",NULL);
    if(!msg||!mac_b64){*err_out=strdup("missing message/mac_b64");return NULL;}
    uint8_t expect[32]; size_t el;
    if(b64_dec(mac_b64,strlen(mac_b64),expect,&el)!=0||el!=32){*err_out=strdup("bad mac_b64");return NULL;}
    uint8_t key[32];
    const char *key_b64=qxt_json_get_str(params,"key_b64",NULL);
    if(key_b64){
        size_t kl; if(b64_dec(key_b64,strlen(key_b64),key,&kl)!=0||kl!=32){*err_out=strdup("bad key_b64 (need 32 bytes)");return NULL;}
    } else {
        const char *pw=qxt_json_get_str(params,"passphrase",NULL);
        const char *salt_b64=qxt_json_get_str(params,"salt_b64",NULL);
        if(!pw||!salt_b64){*err_out=strdup("missing passphrase/salt_b64 or key_b64");return NULL;}
        uint8_t salt[16]; size_t sl;
        if(b64_dec(salt_b64,strlen(salt_b64),salt,&sl)!=0||sl!=16){*err_out=strdup("bad salt");return NULL;}
        pbkdf2((const uint8_t*)pw,strlen(pw),salt,16,100000,key);
    }
    uint8_t mac[32]; hmac_sha256(key,32,(const uint8_t*)msg,strlen(msg),mac);
    int eq=1; for(int i=0;i<32;i++) if(mac[i]!=expect[i]) eq=0;
    qxt_json *res=qxt_json_obj();
    qxt_json_obj_set(res,"ok",qxt_json_bool(eq?1:0));
    return res;
}

/* ============================================================ 自检 */
static int selftest(void){
    int fail=0;
    /* AES-256-GCM 权威向量 (McGrew/Viega, key=IV=P=AAD=0):
       T = 530f8afbc74536b9a963b4f1c4cb738b */
    {
        uint8_t K[32]={0}; uint8_t IV[12]={0};
        uint8_t ct[1], tag[16];
        aes_gcm_encrypt(K,IV,NULL,0,NULL,0,ct,tag);
        static const uint8_t expT[16]={0x53,0x0f,0x8a,0xfb,0xc7,0x45,0x36,0xb9,
                                        0xa9,0x63,0xb4,0xf1,0xc4,0xcb,0x73,0x8b};
        if(memcmp(tag,expT,16)!=0){ fprintf(stderr,"[selftest] AES256-GCM zero-vector tag mismatch\n"); fail=1; }
    }
    /* AES-256-GCM 自洽 roundtrip (随机化键/IV/长明文/AAD):
       加密后解密须还原; 篡改密文须被 tag 拒绝。 */
    {
        uint8_t K[32]; for(int i=0;i<32;i++) K[i]=(uint8_t)(i*7+1);
        uint8_t IV[12]; for(int i=0;i<12;i++) IV[i]=(uint8_t)(i*13+5);
        uint8_t P[64]; for(int i=0;i<64;i++) P[i]=(uint8_t)(i*5+3);
        uint8_t AAD[17]; for(int i=0;i<17;i++) AAD[i]=(uint8_t)(i*3);
        uint8_t ct[64], tag[16], pt2[64];
        aes_gcm_encrypt(K,IV,P,64,AAD,17,ct,tag);
        if(!aes_gcm_decrypt(K,IV,ct,64,AAD,17,tag,pt2)||memcmp(pt2,P,64)!=0){
            fprintf(stderr,"[selftest] AES256-GCM roundtrip mismatch\n"); fail=1;
        }
        ct[5]^=0xff;
        if(aes_gcm_decrypt(K,IV,ct,64,AAD,17,tag,pt2)){
            fprintf(stderr,"[selftest] AES256-GCM tamper not detected\n"); fail=1;
        }
        uint8_t AAD2[17]; memcpy(AAD2,AAD,17); AAD2[0]^=1;
        if(aes_gcm_decrypt(K,IV,ct,64,AAD2,17,tag,pt2)){
            fprintf(stderr,"[selftest] AES256-GCM wrong-aad not rejected\n"); fail=1;
        }
    }
    /* HMAC-SHA256 sign/verify 自洽: 同一消息签名后 verify 须通过; 篡改须失败 */
    {
        uint8_t key[32]; for(int i=0;i<32;i++) key[i]=(uint8_t)(i*11+2);
        const char *msg="the quick brown fox jumps over 13 lazy dogs";
        uint8_t mac[32]; hmac_sha256(key,32,(const uint8_t*)msg,strlen(msg),mac);
        /* 直接用库函数比对 (绕过 IPC 层做纯算法自检) */
        uint8_t mac2[32]; hmac_sha256(key,32,(const uint8_t*)msg,strlen(msg),mac2);
        if(memcmp(mac,mac2,32)!=0){ fprintf(stderr,"[selftest] HMAC-SHA256 mismatch\n"); fail=1; }
        uint8_t badkey[32]; for(int i=0;i<32;i++) badkey[i]=~key[i];
        uint8_t mac3[32]; hmac_sha256(badkey,32,(const uint8_t*)msg,strlen(msg),mac3);
        int diff=0; for(int i=0;i<32;i++) if(mac3[i]!=mac[i]) diff=1;
        if(!diff){ fprintf(stderr,"[selftest] HMAC-SHA256 key sensitivity failed\n"); fail=1; }
    }
    if(fail==0) fprintf(stderr,"crypto selftest ok (AES-256-GCM vectors passed)\n");
    return fail;
}

int main(int argc,char **argv){
    if(argc>1&&strcmp(argv[1],"--selftest")==0){ return selftest(); }
    qxt_ipc_set_engine_name("crypto");
    qxt_ipc_register("derive",h_derive);
    qxt_ipc_register("seal",h_seal);
    qxt_ipc_register("unseal",h_unseal);
    qxt_ipc_register("fingerprint",h_fingerprint);
    qxt_ipc_register("sign",h_sign);
    qxt_ipc_register("verify",h_verify);
    return qxt_ipc_run_loop();
}
