//=============================================================================
// driver_luna.cpp — Custom OpenVR Virtual HMD + Left/Right Hand Controller Driver
//
// Gives Luna full 6-DOF Head (3D tilt/nod/turn/lean) + Left Arm/Hand + Right Arm/Hand
// + native Oculus Touch joystick/button inputs inside SteamVR & VRChat without needing
// a physical VR headset.
//
// Listens on UDP 127.0.0.1:27015 for 46 little-endian floats (184 bytes)
// (also backward-compatible with 25 floats / 100 bytes and 21 floats / 84 bytes):
//   [0..6]   HMD         (x, y, z, qw, qx, qy, qz)
//   [7..13]  Left Hand   (x, y, z, qw, qx, qy, qz)
//   [14..20] Right Hand  (x, y, z, qw, qx, qy, qz)
//   [21..24] Locomotion  (left_joy_x, left_joy_y, right_joy_x, right_a_click)
//   [25..31] Waist       (x, y, z, qw, qx, qy, qz)
//   [32..38] Left Foot   (x, y, z, qw, qx, qy, qz)
//   [39..45] Right Foot  (x, y, z, qw, qx, qy, qz)
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
#include <algorithm>
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

struct LocomotionInput {
    float left_joy_x = 0.0f;
    float left_joy_y = 0.0f;
    float right_joy_x = 0.0f;
    float right_a_click = 0.0f;
};

static std::mutex g_pose_mutex;
// 1.68m eye height matches VRChat's 1.675m PlayerHeight calibration so her legs stand 100% straight!
static Pose7 g_hmd_pose   = {  0.00f, 1.68f,  0.00f, 1.0f, 0.0f, 0.0f, 0.0f };
static Pose7 g_left_pose  = { -0.22f, 1.18f, -0.16f, 1.0f, 0.0f, 0.0f, 0.0f };
static Pose7 g_right_pose = {  0.22f, 1.18f, -0.16f, 1.0f, 0.0f, 0.0f, 0.0f };
static Pose7 g_waist_pose = {  0.00f, 0.96f,  0.00f, 1.0f, 0.0f, 0.0f, 0.0f };
static Pose7 g_lfoot_pose = { -0.10f, 0.06f,  0.00f, 1.0f, 0.0f, 0.0f, 0.0f };
static Pose7 g_rfoot_pose = {  0.10f, 0.06f,  0.00f, 1.0f, 0.0f, 0.0f, 0.0f };
static LocomotionInput g_loco;
static std::atomic<long long> g_last_udp_ms{ 0 };

static long long NowMs() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

static vr::DriverPose_t MakeDriverPose(const Pose7& p, bool is_hmd) {
    (void)is_hmd;
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
    // Direct 6-DOF head pose without neck-model offset shifting eye height down
    pose.shouldApplyHeadModel = false;
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
        vr::PropertyContainerHandle_t c = vr::VRProperties()->TrackedDeviceToPropertyContainer(unObjectId);
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ModelNumber_String, "Luna Virtual HMD");
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ManufacturerName_String, "LunaAI");
        vr::VRProperties()->SetFloatProperty(c, vr::Prop_UserIpdMeters_Float, 0.063f);
        vr::VRProperties()->SetFloatProperty(c, vr::Prop_UserHeadToEyeDepthMeters_Float, 0.0f);
        vr::VRProperties()->SetFloatProperty(c, vr::Prop_DisplayFrequency_Float, 60.0f);
        vr::VRProperties()->SetFloatProperty(c, vr::Prop_SecondsFromVsyncToPhotons_Float, 0.011f);
        vr::VRProperties()->SetUint64Property(c, vr::Prop_CurrentUniverseId_Uint64, 2);
        vr::VRProperties()->SetBoolProperty(c, vr::Prop_IsOnDesktop_Bool, false);
        vr::VRProperties()->SetBoolProperty(c, vr::Prop_DisplayDebugMode_Bool, true);
        vr::VRProperties()->SetBoolProperty(c, vr::Prop_ContainsProximitySensor_Bool, true);
        vr::VRProperties()->SetStringProperty(c, vr::Prop_InputProfilePath_String, "{oculus}/input/rift_profile.json");
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ControllerType_String, "rift");

        vr::VRDriverInput()->CreateBooleanComponent(c, "/proximity", &prox_handle_);
        vr::VRDriverInput()->CreateBooleanComponent(c, "/input/system/click", &sys_handle_);
        vr::VRDriverInput()->UpdateBooleanComponent(prox_handle_, true, 0.0);
        device_index_ = unObjectId;
        return vr::VRInitError_None;
    }

    void Deactivate() override {
        device_index_ = vr::k_unTrackedDeviceIndexInvalid;
        prox_handle_ = vr::k_ulInvalidInputComponentHandle;
        sys_handle_ = vr::k_ulInvalidInputComponentHandle;
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
    void PublishInputs() {
        if (device_index_ == vr::k_unTrackedDeviceIndexInvalid) return;
        if (prox_handle_ != vr::k_ulInvalidInputComponentHandle) {
            vr::VRDriverInput()->UpdateBooleanComponent(prox_handle_, true, 0.0);
        }
        if (sys_handle_ != vr::k_ulInvalidInputComponentHandle) {
            vr::VRDriverInput()->UpdateBooleanComponent(sys_handle_, false, 0.0);
        }
    }

private:
    uint32_t device_index_ = vr::k_unTrackedDeviceIndexInvalid;
    std::unique_ptr<LunaDisplayComponent> display_;
    vr::VRInputComponentHandle_t prox_handle_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t sys_handle_ = vr::k_ulInvalidInputComponentHandle;
};

//=============================================================================
// Virtual Left/Right Hand Controller Driver (LUNA-CTRL-L / LUNA-CTRL-R)
//=============================================================================
class LunaControllerDriver : public vr::ITrackedDeviceServerDriver {
public:
    explicit LunaControllerDriver(vr::ETrackedControllerRole role) : role_(role) {}

    vr::EVRInitError Activate(uint32_t unObjectId) override {
        vr::PropertyContainerHandle_t c = vr::VRProperties()->TrackedDeviceToPropertyContainer(unObjectId);
        bool is_left = (role_ == vr::TrackedControllerRole_LeftHand);
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ModelNumber_String, is_left ? "Luna Left Hand" : "Luna Right Hand");
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ManufacturerName_String, "LunaAI");
        vr::VRProperties()->SetInt32Property(c, vr::Prop_ControllerRoleHint_Int32, role_);
        vr::VRProperties()->SetUint64Property(c, vr::Prop_CurrentUniverseId_Uint64, 2);
        vr::VRProperties()->SetInt32Property(c, vr::Prop_DeviceClass_Int32, vr::TrackedDeviceClass_Controller);
        vr::VRProperties()->SetStringProperty(c, vr::Prop_InputProfilePath_String, "{oculus}/input/touch_profile.json");
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ControllerType_String, "oculus_touch");

        vr::IVRDriverInput* inp = vr::VRDriverInput();
        inp->CreateScalarComponent(c, "/input/joystick/x", &joy_x_, vr::VRScalarType_Absolute, vr::VRScalarUnits_NormalizedTwoSided);
        inp->CreateScalarComponent(c, "/input/joystick/y", &joy_y_, vr::VRScalarType_Absolute, vr::VRScalarUnits_NormalizedTwoSided);
        inp->CreateBooleanComponent(c, "/input/joystick/click", &joy_click_);
        inp->CreateBooleanComponent(c, "/input/joystick/touch", &joy_touch_);

        inp->CreateScalarComponent(c, "/input/trigger/value", &trig_val_, vr::VRScalarType_Absolute, vr::VRScalarUnits_NormalizedOneSided);
        inp->CreateBooleanComponent(c, "/input/trigger/touch", &trig_touch_);

        inp->CreateScalarComponent(c, "/input/grip/value", &grip_val_, vr::VRScalarType_Absolute, vr::VRScalarUnits_NormalizedOneSided);
        inp->CreateBooleanComponent(c, "/input/grip/touch", &grip_touch_);
        inp->CreateBooleanComponent(c, "/input/thumbrest/touch", &thumb_touch_);
        inp->CreateHapticComponent(c, "/output/haptic", &haptic_);

        if (is_left) {
            inp->CreateBooleanComponent(c, "/input/x/click", &btn1_click_);
            inp->CreateBooleanComponent(c, "/input/x/touch", &btn1_touch_);
            inp->CreateBooleanComponent(c, "/input/y/click", &btn2_click_);
            inp->CreateBooleanComponent(c, "/input/y/touch", &btn2_touch_);
            inp->CreateBooleanComponent(c, "/input/system/click", &sys_click_);
            inp->CreateBooleanComponent(c, "/input/system/touch", &sys_touch_);
        } else {
            inp->CreateBooleanComponent(c, "/input/a/click", &btn1_click_);
            inp->CreateBooleanComponent(c, "/input/a/touch", &btn1_touch_);
            inp->CreateBooleanComponent(c, "/input/b/click", &btn2_click_);
            inp->CreateBooleanComponent(c, "/input/b/touch", &btn2_touch_);
        }
        device_index_ = unObjectId;
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
    void PublishInputs(float jx, float jy, bool primary_btn) {
        if (device_index_ == vr::k_unTrackedDeviceIndexInvalid) return;
        if (joy_x_ == vr::k_ulInvalidInputComponentHandle || joy_y_ == vr::k_ulInvalidInputComponentHandle) return;
        vr::IVRDriverInput* inp = vr::VRDriverInput();
        bool stick_active = (std::fabs(jx) > 0.02f || std::fabs(jy) > 0.02f);
        inp->UpdateScalarComponent(joy_x_, jx, 0.0);
        inp->UpdateScalarComponent(joy_y_, jy, 0.0);
        if (joy_touch_ != vr::k_ulInvalidInputComponentHandle) {
            inp->UpdateBooleanComponent(joy_touch_, stick_active, 0.0);
        }
        if (joy_click_ != vr::k_ulInvalidInputComponentHandle) {
            inp->UpdateBooleanComponent(joy_click_, false, 0.0);
        }
        if (btn1_click_ != vr::k_ulInvalidInputComponentHandle) {
            bool press = (role_ == vr::TrackedControllerRole_RightHand) ? primary_btn : false;
            inp->UpdateBooleanComponent(btn1_click_, press, 0.0);
            if (btn1_touch_ != vr::k_ulInvalidInputComponentHandle) {
                inp->UpdateBooleanComponent(btn1_touch_, press, 0.0);
            }
        }
    }

private:
    vr::ETrackedControllerRole role_;
    uint32_t device_index_ = vr::k_unTrackedDeviceIndexInvalid;
    vr::VRInputComponentHandle_t joy_x_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t joy_y_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t joy_click_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t joy_touch_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t trig_val_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t trig_touch_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t grip_val_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t grip_touch_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t thumb_touch_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t haptic_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t btn1_click_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t btn1_touch_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t btn2_click_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t btn2_touch_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t sys_click_ = vr::k_ulInvalidInputComponentHandle;
    vr::VRInputComponentHandle_t sys_touch_ = vr::k_ulInvalidInputComponentHandle;
};

//=============================================================================
// Virtual Generic Tracker Driver (Waist, Left Foot, Right Foot for FBT)
//=============================================================================
class LunaTrackerDriver : public vr::ITrackedDeviceServerDriver {
public:
    LunaTrackerDriver(const char* model, const char* input_profile, const char* controller_type, Pose7* pose_ptr)
        : model_(model), input_profile_(input_profile), controller_type_(controller_type), pose_ptr_(pose_ptr) {}

    vr::EVRInitError Activate(uint32_t unObjectId) override {
        vr::PropertyContainerHandle_t c = vr::VRProperties()->TrackedDeviceToPropertyContainer(unObjectId);
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ModelNumber_String, model_.c_str());
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ManufacturerName_String, "LunaAI");
        vr::VRProperties()->SetInt32Property(c, vr::Prop_ControllerRoleHint_Int32, vr::TrackedControllerRole_OptOut);
        vr::VRProperties()->SetUint64Property(c, vr::Prop_CurrentUniverseId_Uint64, 2);
        vr::VRProperties()->SetInt32Property(c, vr::Prop_DeviceClass_Int32, vr::TrackedDeviceClass_GenericTracker);
        vr::VRProperties()->SetStringProperty(c, vr::Prop_InputProfilePath_String, input_profile_.c_str());
        vr::VRProperties()->SetStringProperty(c, vr::Prop_ControllerType_String, controller_type_.c_str());
        device_index_ = unObjectId;
        return vr::VRInitError_None;
    }

    void Deactivate() override { device_index_ = vr::k_unTrackedDeviceIndexInvalid; }
    void EnterStandby() override {}
    void* GetComponent(const char*) override { return nullptr; }
    void DebugRequest(const char*, char* pchResponseBuffer, uint32_t unResponseBufferSize) override {
        if (unResponseBufferSize >= 1) pchResponseBuffer[0] = 0;
    }
    vr::DriverPose_t GetPose() override {
        std::lock_guard<std::mutex> lock(g_pose_mutex);
        return MakeDriverPose(*pose_ptr_, false);
    }
    void PublishPose() {
        if (device_index_ != vr::k_unTrackedDeviceIndexInvalid) {
            vr::VRServerDriverHost()->TrackedDevicePoseUpdated(device_index_, GetPose(), sizeof(vr::DriverPose_t));
        }
    }

private:
    std::string model_;
    std::string input_profile_;
    std::string controller_type_;
    Pose7* pose_ptr_;
    uint32_t device_index_ = vr::k_unTrackedDeviceIndexInvalid;
};

//=============================================================================
// Device Provider (Spawns HMD + Hands + 3 FBT Trackers & runs UDP + Pose thread)
//=============================================================================
class LunaDeviceProvider : public vr::IServerTrackedDeviceProvider {
public:
    vr::EVRInitError Init(vr::IVRDriverContext* pDriverContext) override {
        VR_INIT_SERVER_DRIVER_CONTEXT(pDriverContext);

        hmd_   = std::make_unique<LunaHMDDriver>();
        left_  = std::make_unique<LunaControllerDriver>(vr::TrackedControllerRole_LeftHand);
        right_ = std::make_unique<LunaControllerDriver>(vr::TrackedControllerRole_RightHand);

        vr::VRServerDriverHost()->TrackedDeviceAdded("LUNA-HMD-001", vr::TrackedDeviceClass_HMD, hmd_.get());
        vr::VRServerDriverHost()->TrackedDeviceAdded("LUNA-CTRL-L", vr::TrackedDeviceClass_Controller, left_.get());
        vr::VRServerDriverHost()->TrackedDeviceAdded("LUNA-CTRL-R", vr::TrackedDeviceClass_Controller, right_.get());

        // Only spawn FBT trackers when LUNA_ENABLE_FBT=1 (otherwise VRChat stays in uncalibrated SixPoint FBT wait)
        const char* fbt_env = std::getenv("LUNA_ENABLE_FBT");
        if (fbt_env && std::strcmp(fbt_env, "1") == 0) {
            waist_ = std::make_unique<LunaTrackerDriver>(
                "Luna Waist Tracker", "{htc}/input/tracker/vive_tracker_waist_profile.json", "vive_tracker_waist", &g_waist_pose);
            lfoot_ = std::make_unique<LunaTrackerDriver>(
                "Luna Left Foot Tracker", "{htc}/input/tracker/vive_tracker_left_foot_profile.json", "vive_tracker_left_foot", &g_lfoot_pose);
            rfoot_ = std::make_unique<LunaTrackerDriver>(
                "Luna Right Foot Tracker", "{htc}/input/tracker/vive_tracker_right_foot_profile.json", "vive_tracker_right_foot", &g_rfoot_pose);
            vr::VRServerDriverHost()->TrackedDeviceAdded("LUNA-TRK-WAIST", vr::TrackedDeviceClass_GenericTracker, waist_.get());
            vr::VRServerDriverHost()->TrackedDeviceAdded("LUNA-TRK-LFOOT", vr::TrackedDeviceClass_GenericTracker, lfoot_.get());
            vr::VRServerDriverHost()->TrackedDeviceAdded("LUNA-TRK-RFOOT", vr::TrackedDeviceClass_GenericTracker, rfoot_.get());
        }

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
        rfoot_.reset();
        lfoot_.reset();
        waist_.reset();
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

        float buf[46];
        while (running_) {
            int n = recv(sock_, reinterpret_cast<char*>(buf), sizeof(buf), 0);
            if (n == 46 * static_cast<int>(sizeof(float)) || n == 25 * static_cast<int>(sizeof(float)) || n == 21 * static_cast<int>(sizeof(float))) {
                std::lock_guard<std::mutex> lock(g_pose_mutex);
                g_hmd_pose   = { buf[0],  buf[1],  buf[2],  buf[3],  buf[4],  buf[5],  buf[6]  };
                g_left_pose  = { buf[7],  buf[8],  buf[9],  buf[10], buf[11], buf[12], buf[13] };
                g_right_pose = { buf[14], buf[15], buf[16], buf[17], buf[18], buf[19], buf[20] };
                if (n >= 25 * static_cast<int>(sizeof(float))) {
                    g_loco.left_joy_x    = std::max(-1.0f, std::min(1.0f, buf[21]));
                    g_loco.left_joy_y    = std::max(-1.0f, std::min(1.0f, buf[22]));
                    g_loco.right_joy_x   = std::max(-1.0f, std::min(1.0f, buf[23]));
                    g_loco.right_a_click = buf[24];
                } else {
                    g_loco = LocomotionInput{};
                }
                if (n == 46 * static_cast<int>(sizeof(float))) {
                    g_waist_pose = { buf[25], buf[26], buf[27], buf[28], buf[29], buf[30], buf[31] };
                    g_lfoot_pose = { buf[32], buf[33], buf[34], buf[35], buf[36], buf[37], buf[38] };
                    g_rfoot_pose = { buf[39], buf[40], buf[41], buf[42], buf[43], buf[44], buf[45] };
                }
                g_last_udp_ms = NowMs();
            }
        }
    }

    void PoseLoop() {
        double t = 0.0;
        while (running_) {
            long long age = NowMs() - g_last_udp_ms.load();
            LocomotionInput loco_copy;
            {
                std::lock_guard<std::mutex> lock(g_pose_mutex);
                if (age > 1500) {
                    // Built-in fallback cutesy-smug breathing & subtle head tilt when Python isn't streaming yet
                    float breath = static_cast<float>(std::sin(t * 1.8) * 0.012);
                    float tilt_z = static_cast<float>(std::sin(t * 0.7) * 0.045);
                    float qw = std::sqrt(std::max(0.0f, 1.0f - tilt_z * tilt_z));
                    g_hmd_pose   = {  0.00f, 1.68f + breath,  0.00f, qw, 0.0f, 0.0f, tilt_z };
                    g_left_pose  = { -0.22f, 1.18f + breath * 0.8f, -0.16f, 0.98f, 0.15f, 0.0f, 0.10f };
                    g_right_pose = {  0.22f, 1.18f + breath * 0.8f, -0.16f, 0.98f, 0.15f, 0.0f, -0.10f };
                    g_waist_pose = {  0.00f, 0.96f + breath * 0.3f,  0.00f, 1.0f, 0.0f, 0.0f, 0.0f };
                    g_lfoot_pose = { -0.10f, 0.06f, 0.00f, 1.0f, 0.0f, 0.0f, 0.0f };
                    g_rfoot_pose = {  0.10f, 0.06f, 0.00f, 1.0f, 0.0f, 0.0f, 0.0f };
                    g_loco = LocomotionInput{};
                }
                loco_copy = g_loco;
            }
            if (hmd_) {
                hmd_->PublishPose();
                hmd_->PublishInputs();
            }
            if (left_) {
                left_->PublishPose();
                left_->PublishInputs(loco_copy.left_joy_x, loco_copy.left_joy_y, false);
            }
            if (right_) {
                right_->PublishPose();
                right_->PublishInputs(loco_copy.right_joy_x, 0.0f, loco_copy.right_a_click > 0.5f);
            }
            if (waist_) waist_->PublishPose();
            if (lfoot_) lfoot_->PublishPose();
            if (rfoot_) rfoot_->PublishPose();

            t += 0.011;
            std::this_thread::sleep_for(std::chrono::milliseconds(11)); // ~90 Hz
        }
    }

    std::unique_ptr<LunaHMDDriver> hmd_;
    std::unique_ptr<LunaControllerDriver> left_;
    std::unique_ptr<LunaControllerDriver> right_;
    std::unique_ptr<LunaTrackerDriver> waist_;
    std::unique_ptr<LunaTrackerDriver> lfoot_;
    std::unique_ptr<LunaTrackerDriver> rfoot_;
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
