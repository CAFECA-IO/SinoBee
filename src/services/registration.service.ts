import { encodeFunctionData, type Hex } from "viem";
import { publicClient } from "@/lib/viem";
import { CONTRACT_ADDRESSES, ABIS } from "@/config/contracts";
import {
  fido2ClientService,
  getRegisterChallenge,
  parsePasskey,
  sendUserOpToBundler,
} from "@/lib/auth/fido2_client";
import {
  encodeWebAuthnSignature,
  hexToBase64Url,
} from "@/lib/auth/crypto_utils";

export type RegistrationStep =
  | "IDLE"
  | "FETCHING_CHALLENGE"
  | "CREATING_PASSKEY"
  | "PARSING_PASSKEY"
  | "PREDICTING_ADDRESS"
  | "CALCULATING_HASH"
  | "AWAITING_SIGNATURE"
  | "DEPLOYING"
  | "SUCCESS"
  | "FAILED";

export class RegistrationService {
  /**
   * Info: (20260116 - Tzuhan) 
   * 核心註冊流程：結合 WebAuthn 與 ERC-4337 部署
   * @param username 用戶暱稱
   * @param onStepChange 狀態回呼，用於更新 UI
   */
  public async signUp(
    username: string,
    onStepChange?: (step: RegistrationStep) => void
  ) {
    try {
      const imageUrl = "default_avatar_url"; // Info: (20260116 - Tzuhan) 未來可從參數傳入

      // Info: (20260116 - Tzuhan) 步驟 1: 獲取挑戰碼
      onStepChange?.("FETCHING_CHALLENGE");
      const regChallenge = await getRegisterChallenge();

      // Info: (20260116 - Tzuhan) 步驟 2: 註冊 Passkey (產生公鑰)
      onStepChange?.("CREATING_PASSKEY");
      const registration = await fido2ClientService.startRegistration({
        user: username,
        challenge: regChallenge,
        userVerification: "required",
        discoverable: "preferred",
      });

      // Info: (20260116 - Tzuhan) 步驟 3: 解析公鑰座標 (X, Y)
      onStepChange?.("PARSING_PASSKEY");
      const { x, y, credentialID } = await parsePasskey(
        registration,
        regChallenge
      );
      const pubKeyX = BigInt(x);
      const pubKeyY = BigInt(y);
      const salt = BigInt(0);

      // Info: (20260116 - Tzuhan) 步驟 4: 預測 SCW 地址 (前端讀取合約)
      onStepChange?.("PREDICTING_ADDRESS");
      if (!CONTRACT_ADDRESSES.FACTORY)
        throw new Error("Factory Address not set");

      const scwAddress = await publicClient.readContract({
        address: CONTRACT_ADDRESSES.FACTORY,
        abi: ABIS.FACTORY,
        functionName: "getAddress",
        args: [pubKeyX, pubKeyY, salt],
      });

      // Info: (20260116 - Tzuhan) 步驟 5: 計算 UserOp Hash
      onStepChange?.("CALCULATING_HASH");
      const factoryCallData = encodeFunctionData({
        abi: ABIS.FACTORY,
        functionName: "createAccount",
        args: [pubKeyX, pubKeyY, salt, credentialID, username, imageUrl],
      });
      const initCode = `${CONTRACT_ADDRESSES.FACTORY}${factoryCallData.slice(
        2
      )}` as Hex;

      const partialUserOp = {
        sender: scwAddress,
        nonce: BigInt(0),
        initCode: initCode,
        callData: "0x" as Hex,
        callGasLimit: BigInt(200_000),
        verificationGasLimit: BigInt(3_500_000),
        preVerificationGas: BigInt(100_000),
        maxFeePerGas: BigInt(0),
        maxPriorityFeePerGas: BigInt(0),
        paymasterAndData: "0x" as Hex,
        signature: "0x" as Hex,
      };

      if (!CONTRACT_ADDRESSES.ENTRY_POINT)
        throw new Error("EntryPoint Address not set");
      const userOpHash = await publicClient.readContract({
        address: CONTRACT_ADDRESSES.ENTRY_POINT,
        abi: ABIS.ENTRY_POINT,
        functionName: "getUserOpHash",
        args: [partialUserOp],
      });

      // Info: (20260116 - Tzuhan) 步驟 6: 二次簽名 (授權部署)
      onStepChange?.("AWAITING_SIGNATURE");
      const challengeBase64 = hexToBase64Url(userOpHash);
      const authentication = await fido2ClientService.startLogin({
        challenge: challengeBase64,
        userVerification: "required",
        timeout: 60000,
      });

      // Info: (20260116 - Tzuhan) 步驟 7: 提交至 Bundler
      onStepChange?.("DEPLOYING");
      const encodedSignature = encodeWebAuthnSignature(
        authentication,
        pubKeyX,
        pubKeyY
      );
      const finalUserOp = {
        ...partialUserOp,
        nonce: `0x${partialUserOp.nonce.toString(16)}`,
        callGasLimit: `0x${partialUserOp.callGasLimit.toString(16)}`,
        verificationGasLimit: `0x${partialUserOp.verificationGasLimit.toString(
          16
        )}`,
        preVerificationGas: `0x${partialUserOp.preVerificationGas.toString(
          16
        )}`,
        maxFeePerGas: `0x${partialUserOp.maxFeePerGas.toString(16)}`,
        maxPriorityFeePerGas: `0x${partialUserOp.maxPriorityFeePerGas.toString(
          16
        )}`,
        signature: encodedSignature,
      };

      const result = await sendUserOpToBundler(
        finalUserOp,
        CONTRACT_ADDRESSES.ENTRY_POINT
      );

      if (result.code === "SUCCESS" || result.success === true) {
        onStepChange?.("SUCCESS");
        return { scwAddress, transactionHash: result.transactionHash };
      } else {
        throw new Error(result.message || "Deployment failed");
      }
    } catch (error) {
      onStepChange?.("FAILED");
      throw error;
    }
  }
}

export const registrationService = new RegistrationService();
