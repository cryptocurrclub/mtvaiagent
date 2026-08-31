// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IERC20Minimal} from "./interfaces/IERC20.sol";
import {IMultiVACLiquidityRouter} from "./interfaces/IMultiVAC.sol";

contract LiquidityRouter is Ownable, ReentrancyGuard {
    address public dexRouter;
    mapping(address => bool) public operators;

    error Unauthorized();
    error InvalidParameters();
    error TokenTransferFailed();

    event LiquidityProvided(address indexed token, address indexed recipient, uint256 nativeUsed, uint256 tokensUsed, uint256 liquidity);

    modifier onlyOperator() {
        if (!operators[msg.sender]) revert Unauthorized();
        _;
    }

    constructor(address initialOwner, address dexRouter_) Ownable(initialOwner) {
        dexRouter = dexRouter_;
        operators[initialOwner] = true;
    }

    receive() external payable {}

    function setOperator(address account, bool enabled) external onlyOwner { operators[account] = enabled; }
    function setDexRouter(address router) external onlyOwner { dexRouter = router; }

    function provideLiquidityNative(
        address token,
        uint256 tokenAmount,
        uint256 minTokenAmount,
        uint256 minNativeAmount,
        address recipient,
        uint256 deadline
    ) external payable onlyOperator nonReentrant returns (uint256 liquidity) {
        if (dexRouter == address(0) || token == address(0) || recipient == address(0) || tokenAmount == 0 || msg.value == 0 || deadline < block.timestamp) {
            revert InvalidParameters();
        }
        if (!IERC20Minimal(token).transferFrom(msg.sender, address(this), tokenAmount)) revert TokenTransferFailed();
        if (!IERC20Minimal(token).approve(dexRouter, tokenAmount)) revert TokenTransferFailed();
        (uint256 nativeUsed, uint256 tokensUsed, uint256 minted) = IMultiVACLiquidityRouter(dexRouter).addLiquidityNative{value: msg.value}(
            token, tokenAmount, minTokenAmount, minNativeAmount, recipient, deadline
        );
        liquidity = minted;
        emit LiquidityProvided(token, recipient, nativeUsed, tokensUsed, minted);
    }
}
