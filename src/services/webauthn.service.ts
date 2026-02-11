import { publicClient } from '@/lib/viem';
import { parseAbiItem } from 'viem';
import { CONTRACT_ADDRESSES } from '@/config/contracts';
import type {
  RegistrationJSON,
  AuthenticationJSON,
  CredentialInfo,
} from '@passwordless-id/webauthn/dist/esm/types';
import { verifyAuthentication, verifyRegistration } from '@/lib/auth/fido2_server';
import { signDeWT } from '@/lib/auth/dewt';
import type { IWebAuthnRepository } from '@/repositories/webauthn.repo';
import { webAuthnRepo } from '@/repositories/webauthn.repo';
import { AppError } from '@/lib/utils/error';
import { ApiCode } from '@/lib/utils/status';
import { extractXYFromSPKI, reconstructKeyFromXY } from '@/lib/auth/crypto_utils';
import { randomBytes } from 'crypto';
import { generateChallengeToken, verifyChallengeToken } from '@/lib/auth/challenge_token';
import type { User } from '@/generated/client';

interface ILoginResult {
  dewt: string;
  user: {
    address: string;
    name: string | null;
    role: string;
  };
}

interface IParsedPublicKey {
  x: string;
  y: string;
  credentialID: string;
}

class WebAuthnService {
  constructor(private readonly repo: IWebAuthnRepository) { }

  public async generateLoginOptions(address: string): Promise<string> {
    const user = await this.ensureUserSynced(address);

    if (!user) {
      throw new AppError(ApiCode.NOT_FOUND, 'User not found on chain. Please register first.');
    }

    const challenge = randomBytes(32).toString('base64url');
    await this.repo.updateChallenge(address, challenge);

    return challenge;
  }

  /**
   * Info: (20251226 - Tzuhan)
   * [Step 2] 驗證登入
   */
  public async loginWithAddress(
    address: string,
    authenticationData: AuthenticationJSON
  ): Promise<ILoginResult> {
    const user = await this.repo.findUserByAddress(address);

    if (!user || !user.pubKeyX || !user.pubKeyY || !user.currentChallenge) {
      throw new AppError(ApiCode.NOT_FOUND, 'User data incomplete. Please retry login flow.');
    }

    const credentialPublicKey = reconstructKeyFromXY(user.pubKeyX, user.pubKeyY);

    const credential: CredentialInfo = {
      id: authenticationData.id,
      publicKey: credentialPublicKey,
      algorithm: 'ES256',
      transports: [],
    };

    try {
      await verifyAuthentication(authenticationData, credential, user.currentChallenge);
    } catch (error) {
      console.error('Login verification failed:', error);
      throw new AppError(ApiCode.UNAUTHORIZED, 'Invalid signature');
    }

    const dewt = await signDeWT(user);
    // Info: (20260123 - Tzuhan) DB 壞掉時這步可能會失敗，但登入應該要算成功
    try {
      await this.repo.updateChallenge(address, '');
    } catch (e) {
      console.warn('[Login] Failed to clear challenge in DB (Non-critical):', e);
    }

    return {
      dewt,
      user: {
        address: user.address,
        name: user.name,
        role: user.role,
      },
    };
  }

  public async parseRegistrationCredential(
    registrationData: RegistrationJSON,
    expectedChallenge: string
  ): Promise<IParsedPublicKey> {
    const verification = await verifyRegistration(registrationData, expectedChallenge);
    const { x, y } = extractXYFromSPKI(verification.credential.publicKey);
    return {
      x: x.toString(),
      y: y.toString(),
      credentialID: verification.credential.id,
    };
  }

  public async ensureUserSynced(address: string) {
    // Info: (20260123 - Tzuhan) 加強容錯：如果 DB 壞了，直接回傳 null 讓後續流程決定是否走鏈上
    try {
      const user = await this.repo.findUserByAddress(address);
      if (user) return user;
    } catch (e) {
      console.warn('[Sync] DB Unavailable, skipping local cache check.', e);
    }

    console.log(`[Sync] Fetching ${address} from chain...`);
    try {
      const logs = await publicClient.getLogs({
        address: CONTRACT_ADDRESSES.FACTORY as `0x${string}`,
        event: parseAbiItem(
          'event AccountCreated(address indexed scw, uint256 pubKeyX, uint256 pubKeyY, uint256 salt, string credentialId,string name, string imageUrl)'
        ),
        args: { scw: address as `0x${string}` },
        fromBlock: 'earliest',
      });

      if (logs.length === 0) return null;

      const { pubKeyX, pubKeyY, credentialId, name, imageUrl } = logs[0].args;

      if (!pubKeyX || !pubKeyY) return null;

      // Info: (20260123 - Tzuhan) 如果 DB 寫入失敗 (連線拒絕)，回傳一個「臨時物件」讓流程繼續
      try {
        return await this.repo.upsertUser({
          address: address,
          pubKeyX: pubKeyX.toString(),
          pubKeyY: pubKeyY.toString(),
          credentialId: credentialId,
          name: name || `User ${address.slice(0, 6)}`,
          imageUrl: imageUrl,
        });
      } catch (dbError) {
        console.warn('[Sync] DB Write Failed (Offline Mode). Returning ephemeral user.', dbError);
        return {
          id: 'ephemeral_id', // Info: (20260202 - Tzuhan) 臨時 ID
          address,
          pubKeyX: pubKeyX.toString(),
          pubKeyY: pubKeyY.toString(),
          credentialId: credentialId || '',
          name: name || `User ${address.slice(0, 6)}`,
          imageUrl: imageUrl || null,
          role: 'USER',
          currentChallenge: null,
          identityAddress: null,
          createdAt: new Date(),
          updatedAt: new Date(),
        } as User;
      }
    } catch (error) {
      console.error('[Sync] Chain fetch failed:', error);
      return null;
    }
  }

  public async generateStatelessLoginOptions() {
    return await generateChallengeToken();
  }

  private async recoverUserByCredentialId(credentialId: string): Promise<User | null> {
    try {
      console.log('[Recovery] Scanning blockchain for AccountCreated events...');

      const logs = await publicClient.getLogs({
        address: CONTRACT_ADDRESSES.FACTORY as `0x${string}`,
        event: parseAbiItem(
          'event AccountCreated(address indexed scw, uint256 pubKeyX, uint256 pubKeyY, uint256 salt, string credentialId, string name, string imageUrl)'
        ),
        fromBlock: 'earliest',
      });

      const matchLog = logs.find((log) => log.args.credentialId === credentialId);

      if (!matchLog) {
        console.log('[Recovery] No matching credential ID found on chain.');
        return null;
      }

      const { scw, pubKeyX, pubKeyY, name, imageUrl } = matchLog.args;
      if (!scw || !pubKeyX || !pubKeyY) return null;

      console.log(`[Recovery] Found user ${scw} on chain. Restoring...`);

      // Info: (20260123 - Tzuhan) 容錯處理：DB 寫入失敗時回傳記憶體物件
      try {
        const user = await this.repo.upsertUser({
          address: scw,
          pubKeyX: pubKeyX.toString(),
          pubKeyY: pubKeyY.toString(),
          credentialId: credentialId,
          name: name || `User ${scw.slice(0, 6)}`,
          imageUrl: imageUrl,
        });
        return user;
      } catch (dbError) {
        console.warn('[Recovery] DB Write Failed (Offline Mode). Using ephemeral data.', dbError);
        return {
          id: `ephemeral_${scw}`,
          address: scw,
          pubKeyX: pubKeyX.toString(),
          pubKeyY: pubKeyY.toString(),
          credentialId: credentialId,
          name: name || `User ${scw.slice(0, 6)}`,
          imageUrl: imageUrl || null,
          role: 'USER',
          currentChallenge: null,
          identityAddress: null,
          createdAt: new Date(),
          updatedAt: new Date(),
        } as User;
      }
    } catch (error) {
      console.error('[Recovery] Failed to recover user:', error);
      return null;
    }
  }

  // Info: (20260105 - Tzuhan) [核心修改] 處理無地址登入 (Discoverable Login)
  public async loginWithCredential(
    challengeToken: string,
    authenticationData: AuthenticationJSON
  ): Promise<ILoginResult> {
    const expectedChallenge = await verifyChallengeToken(challengeToken);

    // Info: (20260123 - Tzuhan) 這裡加上 try-catch，捕捉 DB 連線錯誤
    let user: User | null = null;
    try {
      user = await this.repo.findUserByCredentialId(authenticationData.id);
    } catch (dbError) {
      console.warn(
        '[Login] DB Connection Refused (Index Offline). Falling back to Chain Truth.',
        dbError
      );
      // Info: (20260123 - Tzuhan) user 保持為 null，讓下方的 if (!user) 觸發鏈上救援
    }

    // Info: (20260123 - Tzuhan) 3. 若 DB 找不到 (或 DB 壞掉)，嘗試從鏈上救援
    if (!user) {
      console.log(
        `[Login] Attempting to recover from chain using Credential ID: ${authenticationData.id}`
      );
      user = await this.recoverUserByCredentialId(authenticationData.id);
    }

    if (!user || !user.pubKeyX || !user.pubKeyY) {
      throw new AppError(ApiCode.NOT_FOUND, 'User not found or passkey not registered');
    }

    const credentialPublicKey = reconstructKeyFromXY(user.pubKeyX, user.pubKeyY);
    const credential: CredentialInfo = {
      id: authenticationData.id,
      publicKey: credentialPublicKey,
      algorithm: 'ES256',
      transports: [],
    };

    try {
      await verifyAuthentication(authenticationData, credential, expectedChallenge);
    } catch (error) {
      console.error('Login verification failed:', error);
      throw new AppError(ApiCode.UNAUTHORIZED, 'Invalid signature');
    }

    // Info: (20260105 - Tzuhan) 5. 簽發 DeWT
    const dewt = await signDeWT(user);

    return {
      dewt,
      user: {
        address: user.address,
        name: user.name,
        role: user.role,
      },
    };
  }

  public async verifySignature(
    address: string,
    authenticationData: AuthenticationJSON,
    expectedChallenge: string
  ): Promise<boolean> {
    const user = await this.repo.findUserByAddress(address);

    if (!user || !user.pubKeyX || !user.pubKeyY) {
      throw new AppError(ApiCode.NOT_FOUND, 'User not found or passkey not registered');
    }

    const credentialPublicKey = reconstructKeyFromXY(user.pubKeyX, user.pubKeyY);
    const credential: CredentialInfo = {
      id: authenticationData.id,
      publicKey: credentialPublicKey,
      algorithm: 'ES256',
      transports: [],
    };

    try {
      await verifyAuthentication(authenticationData, credential, expectedChallenge);
      return true;
    } catch (error) {
      console.error('Signature verification failed:', error);
      throw new AppError(ApiCode.UNAUTHORIZED, 'Invalid signature');
    }
  }
}

export const webAuthnService = new WebAuthnService(webAuthnRepo);
