//=============================================================================
// driver_luna.cpp — Custom OpenVR Virtual HMD + Left/Right Hand Controller Driver
//
// Gives Luna full 6-DOF Head (3D tilt/nod/turn/lean) + Left Arm/Hand + Right Arm/Hand
// inside SteamVR & VRChat without needing a physical VR headset.
//
// Listens on UDP 127.0.0.1:27015 for 21 little-endian floats (84 bytes):
//   [0..6]   HMD        (x, y, z, qw, qx, qy, qz)
//   [7..13]  Left Hand  (x, y, z, qw, qx, qy, qz)
//   [14..20] Right Hand (x, y, z, qw, qx, qy, qz)
//=============================================================================
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#pragma comment(lib, "ws2_32.lib")

#include "openvr_driver.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

struct Pose7 {
    float x, y, z;
    float qw, qx, qy, qz;
};

static std::mutex g_pose_mutex;
static Pose7 g_hmd_pose   = {  0.00f, 1.35f,  0.00f, 1.0f, 0.0f, 0.0f, 0.0f };
static Pose7 g_left_pose  = { -0.22f, 0.90f, -0.18f, 1.0f, 0.0f, 0.0f, 0.0f };
static Pose7 g_right_pose = {  0.22f, 0.90f, -0.18f, 1.0f, 0.0f, 0.0f, 0.0f };
static std::atomic<long long> g_last_udp_ms{ 0 };

static long long NowMs() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

static vr::DriverPose_t MakeDriverPose(const Pose7& p, bool is_hmd) {
    vr::DriverPose_t pose = { 0 };
    pose.qWorldFromDriverRotation.w = 1.0;
    pose.qDriverFromHeadRotation.w = 1.0;

    pose.vecPosition[0] = p.x;
    pose.vecPosition[1] = p.y;
    pose.vecPosition[2] = p.z;

    pose.qRotation.w = p.qw;
    pose.qRotation.x = p.qx;
    pose.qRotation.y = p.qy;
    pose.qRotation.z = p.qz;

    pose.poseIsValid = true;
    pose.deviceIsConnected = true;
    pose.result = vr::TrackingResult_Running_OK;
    pose.shouldApplyHeadModel = is_hmd;
    return pose;
}

//=============================================================================
// Virtual Display Component (854x480 lightweight virtual HMD display)
//=============================================================================
class LunaDisplayComponent : public vr::IVRDisplayComponent {
public:
    void GetWindowBounds(int32_t* pnX, int32_t* pnY, uint32_t* pnWidth, uint32_t* pnHeight) override {
        *pnX = 0;
        *pnY = 0;
        *pnWidth = 854;
        *pnHeight = 480;
    }
    bool IsDisplayOnDesktop() override { return true; }
    bool IsDisplayRealDisplay() override { return false; }
    void GetRecommendedRenderTargetSize(uint32_t* pnWidth, uint32_t* pnHeight) override {
        *pnWidth = 854;
        *pnHeight = 480;
    }
    void GetEyeOutputViewport(vr::EVREye eEye, uint32_t* pnX, uint32_t* pnY, uint32_t* pnWidth, uint32_t* pnHeight) override {
        *pnY = 0;
        *pnWidth = 854 / 2;
        *pnHeight = 480;
        *pnX = (eEye == vr::Eye_Left) ? 0 : (854 / 2);
    }
    void GetProjectionRaw(vr::EVREye, float* pfLeft, float* pfRight, float* pfTop, float* pfBottom) override {
        *pfLeft = -1.0f;
        *pfRight = 1.0f;
        *pfTop = -1.0f;
        *pfBottom = 1.0f;
    }
    vr::DistortionCoordinates_t ComputeDistortion(vr::EVREye, float fU, float fV) override {
        vr::DistortionCoordinates_t c{};
        c.rfBlue[0] = fU;  c.rfBlue[1] = fV;
        c.rfGreen[0] = fU; c.rfGreen[1] = fV;
        c.rfRed[0] = fU;   c.rfRed[1] = fV;
        return c;
    }
    bool ComputeInverseDistortion(vr::HmdVector2_t*, vr::EVREye, uint32_t, float, float) override {
        return false;
    }
};

//=============================================================================
// Virtual HMD Device Driver (LUNA-HMD-001)
//=============================================================================
class LunaHMDDriver : public vr::ITrackedDeviceServerDriver {
public:
    LunaHMDDriver() : display_(std::make_unique<LunaDisplayComponent>()) {}

    vr::EVRInitError Activate(uint32_t unObjectId) override {
        device_index_ = unObjectId;
        vr::PropertyContainerHandle_t c = vr::VRProperties()->TrackedDeviceToPropertyContainer(device_index_);
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ModelNumber_String, "Luna Virtual HMD");
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ManufacturerName_String, "LunaAI");
        vr::VRProperties()->SetFloatProperty(c, vr::Prop_UserIpdMeters_Float, 0.063f);
        vr::VRProperties()->SetFloatProperty(c, vr::Prop_UserHeadToEyeDepthMeters_Float, 0.0f);
        vr::VRProperties()->SetFloatProperty(c, vr::Prop_DisplayFrequency_Float, 60.0f);
        vr::VRProperties()->SetFloatProperty(c, vr::Prop_SecondsFromVsyncToPhotons_Float, 0.011f);
        vr::VRProperties()->SetUint64Property(c, vr::Prop_CurrentUniverseId_Uint64, 2);
        vr::VRProperties()->SetBoolProperty(c, vr::Prop_IsOnDesktop_Bool, false);
        vr::VRProperties()->SetBoolProperty(c, vr::Prop_DisplayDebugMode_Bool, true);
        vr::VRProperties()->SetStringProperty(c, vr::Prop_InputProfilePath_String, "{htc}/input/vive_profile.json");
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ControllerType_String, "vive");
        return vr::VRInitError_None;
    }

    void Deactivate() override {
        device_index_ = vr::k_unTrackedDeviceIndexInvalid;
    }
    void EnterStandby() override {}
    void* GetComponent(const char* pchComponentNameAndVersion) override {
        if (0 == std::strcmp(pchComponentNameAndVersion, vr::IVRDisplayComponent_Version)) {
            return display_.get();
        }
        return nullptr;
    }
    void DebugRequest(const char*, char* pchResponseBuffer, uint32_t unResponseBufferSize) override {
        if (unResponseBufferSize >= 1) pchResponseBuffer[0] = 0;
    }
    vr::DriverPose_t GetPose() override {
        std::lock_guard<std::mutex> lock(g_pose_mutex);
        return MakeDriverPose(g_hmd_pose, true);
    }
    void PublishPose() {
        if (device_index_ != vr::k_unTrackedDeviceIndexInvalid) {
            vr::VRServerDriverHost()->TrackedDevicePoseUpdated(device_index_, GetPose(), sizeof(vr::DriverPose_t));
        }
    }

private:
    uint32_t device_index_ = vr::k_unTrackedDeviceIndexInvalid;
    std::unique_ptr<LunaDisplayComponent> display_;
};

//=============================================================================
// Virtual Left/Right Hand Controller Driver (LUNA-CTRL-L / LUNA-CTRL-R)
//=============================================================================
class LunaControllerDriver : public vr::ITrackedDeviceServerDriver {
public:
    explicit LunaControllerDriver(vr::ETrackedControllerRole role) : role_(role) {}

    vr::EVRInitError Activate(uint32_t unObjectId) override {
        device_index_ = unObjectId;
        vr::PropertyContainerHandle_t c = vr::VRProperties()->TrackedDeviceToPropertyContainer(device_index_);
        bool is_left = (role_ == vr::TrackedControllerRole_LeftHand);
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ModelNumber_String, is_left ? "Luna Left Hand" : "Luna Right Hand");
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ManufacturerName_String, "LunaAI");
        vr::VRProperties()->SetInt32Property(c, vr::Prop_ControllerRoleHint_Int32, role_);
        vr::VRProperties()->SetUint64Property(c, vr::Prop_CurrentUniverseId_Uint64, 2);
        vr::VRProperties()->SetInt32Property(c, vr::Prop_DeviceClass_Int32, vr::TrackedDeviceClass_Controller);
        vr::VRProperties()->SetStringProperty(c, vr::Prop_InputProfilePath_String, "{oculus}/input/touch_profile.json");
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ControllerType_String, "oculus_touch");
        return vr::VRInitError_None;
    }

    void Deactivate() override {
        device_index_ = vr::k_unTrackedDeviceIndexInvalid;
    }
    void EnterStandby() override {}
    void* GetComponent(const char*) override { return nullptr; }
    void DebugRequest(const char*, char* pchResponseBuffer, uint32_t unResponseBufferSize) override {
        if (unResponseBufferSize >= 1) pchResponseBuffer[0] = 0;
    }
    vr::DriverPose_t GetPose() override {
        std::lock_guard<std::mutex> lock(g_pose_mutex);
        const Pose7& p = (role_ == vr::TrackedControllerRole_LeftHand) ? g_left_pose : g_right_pose;
        return MakeDriverPose(p, false);
    }
    void PublishPose() {
        if (device_index_ != vr::k_unTrackedDeviceIndexInvalid) {
            vr::VRServerDriverHost()->TrackedDevicePoseUpdated(device_index_, GetPose(), sizeof(vr::DriverPose_t));
        }
    }

private:
    vr::ETrackedControllerRole role_;
    uint32_t device_index_ = vr::k_unTrackedDeviceIndexInvalid;
};

//=============================================================================
// Device Provider (Spawns HMD + Left Hand + Right Hand & runs UDP + Pose thread)
//=============================================================================
class LunaDeviceProvider : public vr::IServerTrackedDeviceProvider {
public:
    vr::EVRInitError Init(vr::IVRDriverContext* pDriverContext) override {
        VR_INIT_SERVER_DRIVER_CONTEXT(pDriverContext);

        hmd_ = std::make_unique<LunaHMDDriver>();
        left_ = std::make_unique<LunaControllerDriver>(vr::TrackedControllerRole_LeftHand);
        right_ = std::make_unique<LunaControllerDriver>(vr::TrackedControllerRole_RightHand);

        vr::VRServerDriverHost()->TrackedDeviceAdded("LUNA-HMD-001", vr::TrackedDeviceClass_HMD, hmd_.get());
        vr::VRServerDriverHost()->TrackedDeviceAdded("LUNA-CTRL-L", vr::TrackedDeviceClass_Controller, left_.get());
        vr::VRServerDriverHost()->TrackedDeviceAdded("LUNA-CTRL-R", vr::TrackedDeviceClass_Controller, right_.get());

        running_ = true;
        udp_thread_ = std::thread(&LunaDeviceProvider::UdpLoop, this);
        pose_thread_ = std::thread(&LunaDeviceProvider::PoseLoop, this);
        return vr::VRInitError_None;
    }

    void Cleanup() override {
        running_ = false;
        if (sock_ != INVALID_SOCKET) {
            closesocket(sock_);
            sock_ = INVALID_SOCKET;
        }
        if (udp_thread_.joinable()) udp_thread_.join();
        if (pose_thread_.joinable()) pose_thread_.join();
        right_.reset();
        left_.reset();
        hmd_.reset();
    }

    const char* const* GetInterfaceVersions() override { return vr::k_InterfaceVersions; }
    void RunFrame() override {
        vr::VREvent_t evt{};
        while (vr::VRServerDriverHost()->PollNextEvent(&evt, sizeof(evt))) {}
    }
    bool ShouldBlockStandbyMode() override { return false; }
    void EnterStandby() override {}
    void LeaveStandby() override {}

private:
    void UdpLoop() {
        WSADATA wsa{};
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return;

        sock_ = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (sock_ == INVALID_SOCKET) return;

        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(27015);
        inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);

        if (bind(sock_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == SOCKET_ERROR) {
            closesocket(sock_);
            sock_ = INVALID_SOCKET;
            return;
        }

        float buf[21];
        while (running_) {
            int n = recv(sock_, reinterpret_cast<char*>(buf), sizeof(buf), 0);
            if (n == sizeof(buf)) {
                std::lock_guard<std::mutex> lock(g_pose_mutex);
                g_hmd_pose   = { buf[0],  buf[1],  buf[2],  buf[3],  buf[4],  buf[5],  buf[6]  };
                g_left_pose  = { buf[7],  buf[8],  buf[9],  buf[10], buf[11], buf[12], buf[13] };
                g_right_pose = { buf[14], buf[15], buf[16], buf[17], buf[18], buf[19], buf[20] };
                g_last_udp_ms = NowMs();
            }
        }
    }

    void PoseLoop() {
        double t = 0.0;
        while (running_) {
            long long age = NowMs() - g_last_udp_ms.load();
            if (age > 1500) {
                // Built-in fallback cutesy-smug breathing & subtle head tilt when Python isn't streaming yet
                float breath = static_cast<float>(std::sin(t * 1.8) * 0.012);
                float tilt_z = static_cast<float>(std::sin(t * 0.7) * 0.045); // ~5 deg subtle head tilt
                float qw = std::sqrt(std::max(0.0f, 1.0f - tilt_z * tilt_z));
                std::lock_guard<std::mutex> lock(g_pose_mutex);
                g_hmd_pose   = {  0.00f, 1.35f + breath,  0.00f, qw, 0.0f, 0.0f, tilt_z };
                g_left_pose  = { -0.20f, 0.92f + breath * 0.8f, -0.18f, 0.98f, 0.15f, 0.0f, 0.10f };
                g_right_pose = {  0.20f, 0.92f + breath * 0.8f, -0.18f, 0.98f, 0.15f, 0.0f, -0.10f };
            }
            if (hmd_)   hmd_->PublishPose();
            if (left_)  left_->PublishPose();
            if (right_) right_->PublishPose();

            t += 0.011;
            std::this_thread::sleep_for(std::chrono::milliseconds(11)); // ~90 Hz
        }
    }

    std::unique_ptr<LunaHMDDriver> hmd_;
    std::unique_ptr<LunaControllerDriver> left_;
    std::unique_ptr<LunaControllerDriver> right_;
    std::atomic<bool> running_{ false };
    SOCKET sock_ = INVALID_SOCKET;
    std::thread udp_thread_;
    std::thread pose_thread_;
};

static LunaDeviceProvider g_luna_provider;

extern "C" __declspec(dllexport) void* HmdDriverFactory(const char* pInterfaceName, int* pReturnCode) {
    if (0 == std::strcmp(vr::IServerTrackedDeviceProvider_Version, pInterfaceName)) {
        return &g_luna_provider;
    }
    if (pReturnCode) {
        *pReturnCode = vr::VRInitError_Init_InterfaceNotFound;
    }
    return nullptr;
}
