// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IMultiVACShardMessenger} from "./interfaces/IMultiVAC.sol";

contract CrossShardGateway is Ownable {
    address public messenger;
    mapping(address => bool) public senders;

    error Unauthorized();
    error InvalidMessage();

    event CrossShardMessageSent(bytes32 indexed messageId, uint32 indexed destinationShard, bytes payload);

    modifier onlySender() {
        if (!senders[msg.sender]) revert Unauthorized();
        _;
    }

    constructor(address initialOwner, address messenger_) Ownable(initialOwner) {
        messenger = messenger_;
        senders[initialOwner] = true;
    }

    function setSender(address sender, bool enabled) external onlyOwner { senders[sender] = enabled; }
    function setMessenger(address messenger_) external onlyOwner { messenger = messenger_; }

    function send(uint32 destinationShard, bytes calldata payload) external payable onlySender returns (bytes32 messageId) {
        if (messenger == address(0) || payload.length == 0) revert InvalidMessage();
        messageId = IMultiVACShardMessenger(messenger).sendMessage{value: msg.value}(destinationShard, payload);
        emit CrossShardMessageSent(messageId, destinationShard, payload);
    }
}
