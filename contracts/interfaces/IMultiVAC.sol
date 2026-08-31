// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IMultiVACShardMessenger {
    function sendMessage(uint32 destinationShard, bytes calldata payload) external payable returns (bytes32 messageId);
}

interface IMultiVACLiquidityRouter {
    function addLiquidityNative(
        address token,
        uint256 tokenAmount,
        uint256 minTokenAmount,
        uint256 minNativeAmount,
        address recipient,
        uint256 deadline
    ) external payable returns (uint256 nativeUsed, uint256 tokensUsed, uint256 liquidity);
}
