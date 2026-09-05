// SPDX-License-Identifier: GPL-3.0-or-later
// Exercise the actual native update/propagation/marginalization, not a mock EKF.
#include <iostream>
#include "passive_anchors.h"

using ov_msckf::State;
using ov_msckf::StateHelper;

void require(bool ok, const char *message) {
  if (!ok) throw std::runtime_error(message);
}

template <class F> void rejects(F operation) {
  bool rejected = false;
  try { operation(); } catch (const std::runtime_error &) { rejected = true; }
  require(rejected, "Invalid anchor operation was accepted");
}

int main() {
  try {
    ov_msckf::StateOptions options;
    auto reference = std::make_shared<State>(options);
    auto retained = std::make_shared<State>(options);
    Eigen::MatrixXd initial = Eigen::MatrixXd::Identity(15,15)*.1;
    for (auto state : {reference, retained}) {
      StateHelper::set_initial_covariance(state, initial, {state->_imu});
      state->_timestamp = 1.;
      StateHelper::augment_clone(state, Eigen::Vector3d::Zero());
    }
    PassivePoseAnchors anchors(retained, 1);
    anchors.retain(1000000000, 1.);
    auto anchor = anchors.anchors().at(1000000000).pose;
    require(StateHelper::get_full_covariance(retained).rows() == 27, "Wrong augmented dimension");
    auto joint = StateHelper::get_marginal_covariance(retained, {retained->_imu->pose(), anchor});
    require((joint.topLeftCorner(6,6)-joint.topRightCorner(6,6)).norm() < 1e-14,
            "Clone cross covariance was lost");
    rejects([&] { anchors.retain(1000000000, 1.); });
    rejects([&] { anchors.retain(2000000000, 2.); });
    rejects([&] { anchors.release(2000000000); });

    // The ordinary camera clone disappears; the passive variable stays.
    for (auto state : {reference, retained}) {
      StateHelper::marginalize(state, state->_clones_IMU.at(1.));
      state->_clones_IMU.erase(1.);
    }
    for (int step=0; step<20; ++step) {
      for (auto state : {reference, retained}) {
        Eigen::MatrixXd phi = Eigen::MatrixXd::Identity(15,15);
        phi.block<3,3>(3,6) = Eigen::Matrix3d::Identity()*.05;
        StateHelper::EKFPropagation(state, {state->_imu}, {state->_imu}, phi,
                                   Eigen::MatrixXd::Identity(15,15)*.001);
        Eigen::MatrixXd h = Eigen::MatrixXd::Zero(3,15);
        h.block<3,3>(0,3).setIdentity();
        StateHelper::EKFUpdate(state, {state->_imu}, h, Eigen::Vector3d(.002,-.003,.001),
                              Eigen::Matrix3d::Identity()*.03);
      }
      require((reference->_imu->value()-retained->_imu->value()).norm() < 1e-12,
              "Passive anchor changed local mean");
      require((StateHelper::get_marginal_covariance(reference, {reference->_imu})-
               StateHelper::get_marginal_covariance(retained, {retained->_imu})).norm() < 1e-12,
              "Passive anchor changed local covariance");
    }
    auto after = StateHelper::get_marginal_covariance(retained, {retained->_imu->pose(), anchor});
    require(after.topRightCorner(6,6).norm() > .001, "Retired-time cross covariance missing");
    require(anchor->pos().norm() > .001, "Passive mean did not receive correlated visual updates");
    require((after.bottomRightCorner(6,6)-joint.bottomRightCorner(6,6)).norm() > .001,
            "Passive covariance remained frozen");
    auto before_release = StateHelper::get_marginal_covariance(retained, {retained->_imu});
    anchors.release(1000000000);
    require(StateHelper::get_full_covariance(retained).rows() == 15, "Release did not bound state size");
    require((before_release-StateHelper::get_marginal_covariance(retained, {retained->_imu})).norm() < 1e-14,
            "Release changed live marginal");
    rejects([&] { anchors.retain(1000000000, 1.); });
    std::cout << "Passive anchor: preserved native mean/covariance; correlated updates and release verified\n";
    return 0;
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
