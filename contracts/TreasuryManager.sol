// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface IMtvFaucet {
    function fund(address recipient, uint256 amount) external;
}

contract TreasuryManager is Ownable, ReentrancyGuard {
    struct FundingRequest {
        address payable recipient;
        uint256 amount;
        uint64 deadline;
        uint8 approvals;
        bool executed;
    }

    uint256 public immutable maxFundingPerRequest;
    uint8 public immutable requiredApprovals;
    uint256 public nextRequestId;
    address public faucet;
    mapping(address => bool) public operators;
    mapping(address => bool) public approvers;
    mapping(uint256 => FundingRequest) public requests;
    mapping(uint256 => mapping(address => bool)) public approvedBy;

    error Unauthorized();
    error InvalidRequest();
    error AlreadyApproved();
    error NotReady();
    error TransferFailed();

    event FundingRequested(uint256 indexed requestId, address indexed recipient, uint256 amount, uint64 deadline);
    event FundingApproved(uint256 indexed requestId, address indexed approver);
    event FundingExecuted(uint256 indexed requestId, address indexed recipient, uint256 amount);
    event FaucetUpdated(address indexed faucet);

    modifier onlyOperator() {
        if (!operators[msg.sender]) revert Unauthorized();
        _;
    }

    modifier onlyApprover() {
        if (!approvers[msg.sender]) revert Unauthorized();
        _;
    }

    constructor(address initialOwner, uint256 maxFundingPerRequest_, uint8 requiredApprovals_)
        Ownable(initialOwner)
    {
        require(requiredApprovals_ > 0, "approvals is zero");
        maxFundingPerRequest = maxFundingPerRequest_;
        requiredApprovals = requiredApprovals_;
        operators[initialOwner] = true;
        approvers[initialOwner] = true;
    }

    receive() external payable {}

    function setOperator(address account, bool enabled) external onlyOwner { operators[account] = enabled; }
    function setApprover(address account, bool enabled) external onlyOwner { approvers[account] = enabled; }

    function setFaucet(address faucet_) external onlyOwner {
        faucet = faucet_;
        emit FaucetUpdated(faucet_);
    }

    function requestFunding(address payable recipient, uint256 amount, uint64 deadline)
        external onlyOperator returns (uint256 requestId)
    {
        if (recipient == address(0) || amount == 0 || amount > maxFundingPerRequest || deadline <= block.timestamp) {
            revert InvalidRequest();
        }
        requestId = nextRequestId++;
        requests[requestId] = FundingRequest(recipient, amount, deadline, 0, false);
        emit FundingRequested(requestId, recipient, amount, deadline);
    }

    function approveFunding(uint256 requestId) external onlyApprover {
        FundingRequest storage request = requests[requestId];
        if (request.recipient == address(0) || request.executed || block.timestamp > request.deadline) revert InvalidRequest();
        if (approvedBy[requestId][msg.sender]) revert AlreadyApproved();
        approvedBy[requestId][msg.sender] = true;
        request.approvals++;
        emit FundingApproved(requestId, msg.sender);
    }

    function executeFunding(uint256 requestId) external nonReentrant {
        FundingRequest storage request = requests[requestId];
        if (request.recipient == address(0) || request.executed || block.timestamp > request.deadline || request.approvals < requiredApprovals) {
            revert NotReady();
        }
        request.executed = true;
        (bool success,) = request.recipient.call{value: request.amount}("");
        if (!success) revert TransferFailed();
        emit FundingExecuted(requestId, request.recipient, request.amount);
    }

    function fundFromFaucet(address recipient, uint256 amount) external onlyOperator {
        if (faucet == address(0) || recipient == address(0) || amount == 0 || amount > maxFundingPerRequest) revert InvalidRequest();
        IMtvFaucet(faucet).fund(recipient, amount);
    }
}
