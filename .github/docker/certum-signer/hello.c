/* Throwaway PE used by the "Windows Signing Smoke Test" workflow: an
 * auditable, repo-sourced exe to sign instead of shipping an opaque binary
 * in the signer image. Compiled by the mingw stage of the Dockerfile. */
#include <stdio.h>

int main(void) {
    puts("pixlstash signing smoke test");
    return 0;
}
