"use client";

import { useState } from "react";
import {
  Dialog,
  DialogPanel,
  DialogTitle,
  Transition,
  TransitionChild,
} from "@headlessui/react";
import { X, User } from "lucide-react";
import { useTranslation } from "@/i18n/i18n_context";
import LegalModal from "@/components/common/legal_modal";
import { useAuth } from "@/contexts/auth_context";
import { useRouter, usePathname } from "next/navigation";
import {
  fido2ClientService,
  getLoginOptions,
  verifyLogin,
} from "@/lib/auth/fido2_client";

import {
  registrationService,
  RegistrationStep,
} from "@/services/registration.service";
import AuthTransition, { LoginStep } from "@/components/auth/auth_transition";

interface IAuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

type AuthMode = "login" | "register";

export default function AuthModal({ isOpen, onClose, onSuccess }: IAuthModalProps) {
  const { t } = useTranslation();
  const router = useRouter();
  const pathname = usePathname();
  const { refreshAuth } = useAuth();
  const [mode, setMode] = useState<AuthMode>("login");
  const [loading, setLoading] = useState(false);
  const [username, setUsername] = useState("");
  const [agreedToTos, setAgreedToTos] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [legalDoc, setLegalDoc] = useState<
    "terms_of_service" | "privacy_policy" | null
  >(null);
  const [currentStep, setCurrentStep] = useState<RegistrationStep>("IDLE");
  const [loginStep, setLoginStep] = useState<LoginStep>("IDLE");

  const handleLogin = async () => {
    setLoading(true);
    setError(null);
    setLoginStep("FETCHING_CHALLENGE");
    try {
      // Info: (20260116 - Tzuhan)  1. 取得 Stateless Challenge
      const { challenge, token } = await getLoginOptions();

      // Info: (20260105 - Tzuhan) 2. 喚起 Passkey
      setLoginStep("AUTHENTICATING");
      const authentication = await fido2ClientService.startLogin({
        challenge: challenge,
        userVerification: "required",
        timeout: 60000,
        // Info: (20260105 - Tzuhan) 不傳 allowCredentials，啟用探索模式
      });

      // Info: (20260105 - Tzuhan) 3. 驗證並登入
      setLoginStep("VERIFYING");
      const payload = await verifyLogin(token!, authentication);

      // Info: (20260105 - Tzuhan) 4. 成功
      localStorage.setItem("dewt", payload.dewt);
      localStorage.setItem("user_address", payload.user.address);

      await refreshAuth();
      setLoginStep("SUCCESS");
      // Info: (20260116 - Tzuhan) Add a small delay for user to see success message
      await new Promise((resolve) => setTimeout(resolve, 1500));
      if (onSuccess) onSuccess();
      onClose();
      // Info: (20260118 - Luphia) Redirect to dashboard if at /
      if (pathname === "/") {
        router.push('/user/main');
      }
    } catch (err: unknown) {
      console.error("Login error:", err);
      const message = err instanceof Error ? err.message : "Login failed";
      setError(message);
      setLoginStep("FAILED");
    } finally {
      if (loginStep !== "SUCCESS") {
        setLoading(false);
      }
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!agreedToTos) {
      setError(t("auth_modal.tos_required"));
      return;
    }
    if (!username) {
      setError(t("auth_modal.username_required"));
      return;
    }

    setLoading(true);
    setError(null);

    try {
      await registrationService.signUp(
        username.trim(),
        (step) => setCurrentStep(step) // Info: (20260116 - Tzuhan) 更新 UI 狀態
      );
      await refreshAuth();
      // Info: (20260116 - Luphia) Add a small delay for user to see success message
      await new Promise((resolve) => setTimeout(resolve, 1500));
      if (onSuccess) onSuccess();
      onClose();
      // Info: (20260118 - Luphia) Redirect to dashboard if at /
      if (pathname === "/") {
        router.push('/user/main');
      }
    } catch (err: unknown) {
      console.error("Registration error:", err);
      const message =
        err instanceof Error ? err.message : "Registration failed";
      setError(message);
    } finally {
      // Info: (20260116 - Luphia) Only set loading false if not successful/closed yet, but since we await delay on success, we might want to keep it loading.
      // Info: (20260116 - Luphia) Actually registrationService sets "SUCCESS" step, so we can rely on that.
      if (currentStep !== "SUCCESS") {
        setLoading(false);
      }
    }
  };

  return (
    <>
      <Transition show={isOpen}>
        <Dialog className="relative z-50" onClose={onClose}>
          <TransitionChild
            enter="ease-out duration-300"
            enterFrom="opacity-0"
            enterTo="opacity-100"
            leave="ease-in duration-200"
            leaveFrom="opacity-100"
            leaveTo="opacity-0"
          >
            <div className="fixed inset-0 bg-gray-500/75 transition-opacity" />
          </TransitionChild>

          <div className="fixed inset-0 z-10 w-screen overflow-y-auto">
            <div className="flex min-h-full items-center justify-center p-4 text-center sm:p-0">
              <TransitionChild
                enter="ease-out duration-300"
                enterFrom="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
                enterTo="opacity-100 translate-y-0 sm:scale-100"
                leave="ease-in duration-200"
                leaveFrom="opacity-100 translate-y-0 sm:scale-100"
                leaveTo="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
              >
                <DialogPanel className="relative transform overflow-hidden rounded-lg bg-white px-4 pb-4 pt-5 text-left shadow-xl transition-all w-full sm:my-8 sm:max-w-md sm:p-6">
                  <div className="absolute right-0 top-0 hidden pr-4 pt-4 sm:block">
                    <button
                      type="button"
                      className="rounded-md bg-white text-gray-400 hover:text-gray-500 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:ring-offset-2"
                      onClick={onClose}
                    >
                      <span className="sr-only">Close</span>
                      <X className="h-6 w-6" aria-hidden="true" />
                    </button>
                  </div>

                  <div className="sm:mx-auto sm:w-full sm:max-w-md">
                    <div className="text-center mb-8">
                      <DialogTitle
                        as="h3"
                        className="text-2xl font-bold leading-9 tracking-tight text-gray-900"
                      >
                        {mode === "login"
                          ? t("auth_modal.welcome_back")
                          : t("auth_modal.create_account")}
                      </DialogTitle>
                    </div>

                    {/* Info: (20260103 - Luphia) Tabs */}
                    <div className="flex border-b border-gray-200 mb-6">
                      <button
                        type="button"
                        disabled={loading}
                        className={`flex-1 pb-2 text-center font-medium transition-colors ${mode === "login"
                          ? "text-orange-600 border-b-2 border-orange-600"
                          : "text-gray-500 hover:text-gray-700"
                          } ${loading ? "opacity-50 cursor-not-allowed hover:text-gray-500" : ""}`}
                        onClick={() => {
                          if (loading) return;
                          setMode("login");
                          setError(null);
                        }}
                      >
                        {t("auth_modal.login_tab")}
                      </button>
                      <button
                        type="button"
                        disabled={loading}
                        className={`flex-1 pb-2 text-center font-medium transition-colors ${mode === "register"
                          ? "text-orange-600 border-b-2 border-orange-600"
                          : "text-gray-500 hover:text-gray-700"
                          } ${loading ? "opacity-50 cursor-not-allowed hover:text-gray-500" : ""}`}
                        onClick={() => {
                          if (loading) return;
                          setMode("register");
                          setError(null);
                        }}
                      >
                        {t("auth_modal.register_tab")}
                      </button>
                    </div>

                    {/* Info: (20260103 - Luphia) Error Message */}
                    {error && (
                      <div className="mb-4 rounded-md bg-red-50 p-4">
                        <div className="flex">
                          <div className="ml-3">
                            <h3 className="text-sm font-medium text-red-800">
                              {error}
                            </h3>
                          </div>
                        </div>
                      </div>
                    )}

                    {mode === "login" ? (
                      <>
                        {loading ? (
                          <AuthTransition mode="login" step={loginStep} />
                        ) : (
                          <div className="space-y-6">
                            <div className="text-sm text-gray-500 text-center">
                              {t("auth_modal.login_desc")}
                            </div>
                            <button
                              onClick={handleLogin}
                              disabled={loading}
                              className="flex w-full justify-center rounded-md bg-orange-600 px-3 py-1.5 text-sm font-semibold leading-6 text-white shadow-sm hover:bg-orange-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-600 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              {loading
                                ? t("auth_modal.authenticating")
                                : t("auth_modal.login_btn")}
                            </button>
                          </div>
                        )}
                      </>
                    ) : (
                      <>
                        {loading ? (
                          <AuthTransition
                            mode="register"
                            step={currentStep}
                          />
                        ) : (
                          <form onSubmit={handleRegister} className="space-y-6">
                            <div>
                              <label
                                htmlFor="username"
                                className="block text-sm font-medium leading-6 text-gray-900"
                              >
                                {t("auth_modal.username")}
                              </label>
                              <div className="mt-2 relative">
                                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                                  <User className="h-5 w-5 text-gray-400" />
                                </div>
                                <input
                                  id="username"
                                  name="username"
                                  type="text"
                                  required
                                  value={username}
                                  onChange={(e) => setUsername(e.target.value)}
                                  className="block w-full rounded-md border-0 py-1.5 pl-10 text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-orange-600 sm:text-sm sm:leading-6"
                                  placeholder={t(
                                    "auth_modal.username_placeholder"
                                  )}
                                  aria-label={t("auth_modal.username")}
                                />
                              </div>
                            </div>

                            <div className="relative flex items-start">
                              <div className="flex h-6 items-center">
                                <input
                                  id="tos"
                                  name="tos"
                                  type="checkbox"
                                  checked={agreedToTos}
                                  onChange={(e) =>
                                    setAgreedToTos(e.target.checked)
                                  }
                                  className="h-4 w-4 rounded border-gray-300 text-orange-600 focus:ring-orange-600"
                                  aria-label={
                                    t("auth_modal.tos_agree") +
                                    " " +
                                    t("auth_modal.tos_link")
                                  }
                                />
                              </div>
                              <div className="ml-3 text-sm leading-6">
                                <label
                                  htmlFor="tos"
                                  className="font-medium text-gray-900"
                                >
                                  {t("auth_modal.tos_agree")}{" "}
                                  <button
                                    type="button"
                                    className="font-semibold text-orange-600 hover:text-orange-500 underline decoration-transparent hover:decoration-orange-500 transition-all"
                                    onClick={() =>
                                      setLegalDoc("terms_of_service")
                                    }
                                  >
                                    {t("auth_modal.tos_link")}
                                  </button>{" "}
                                  {t("auth_modal.and")}{" "}
                                  <button
                                    type="button"
                                    className="font-semibold text-orange-600 hover:text-orange-500 underline decoration-transparent hover:decoration-orange-500 transition-all"
                                    onClick={() =>
                                      setLegalDoc("privacy_policy")
                                    }
                                  >
                                    {t("auth_modal.privacy_link")}
                                  </button>
                                </label>
                              </div>
                            </div>

                            <button
                              type="submit"
                              disabled={loading}
                              className="flex w-full justify-center rounded-md bg-orange-600 px-3 py-1.5 text-sm font-semibold leading-6 text-white shadow-sm hover:bg-orange-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-600 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              {loading
                                ? t(`auth_modal.${currentStep.toLowerCase()}`)
                                : t("auth_modal.create_btn")}
                            </button>
                          </form>
                        )}
                      </>
                    )}
                  </div>
                </DialogPanel>
              </TransitionChild>
            </div>
          </div>
        </Dialog>
      </Transition>

      <LegalModal
        isOpen={!!legalDoc}
        onClose={() => setLegalDoc(null)}
        documentType={legalDoc}
      />
    </>
  );
}
