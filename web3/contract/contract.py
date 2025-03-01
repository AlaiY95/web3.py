import copy
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    cast,
)

from eth_typing import (
    ChecksumAddress,
)
from eth_utils import (
    combomethod,
)
from eth_utils.toolz import (
    partial,
)
from hexbytes import (
    HexBytes,
)

from web3._utils.datatypes import (
    PropertyCheckingFactory,
)
from web3._utils.events import (
    EventFilterBuilder,
    get_event_data,
)
from web3._utils.filters import (
    LogFilter,
)
from web3._utils.normalizers import (
    normalize_abi,
    normalize_address,
    normalize_bytecode,
)
from web3._utils.transactions import (
    fill_transaction_defaults,
)
from web3.contract.base_contract import (
    BaseContract,
    BaseContractCaller,
    BaseContractConstructor,
    BaseContractEvent,
    BaseContractEvents,
    BaseContractFunction,
    BaseContractFunctions,
    parse_block_identifier,
)
from web3.contract.utils import (
    build_transaction_for_function,
    call_contract_function,
    estimate_gas_for_function,
    find_functions_by_identifier,
    get_function_by_identifier,
    transact_with_contract_function,
)
from web3.types import (  # noqa: F401
    ABI,
    BlockIdentifier,
    CallOverride,
    EventData,
    TxParams,
)

if TYPE_CHECKING:
    from ens import ENS  # noqa: F401
    from web3 import Web3  # noqa: F401


class ContractFunctions(BaseContractFunctions):
    def __init__(
        self,
        abi: ABI,
        w3: "Web3",
        address: Optional[ChecksumAddress] = None,
        decode_tuples: Optional[bool] = False,
    ) -> None:
        super().__init__(abi, w3, ContractFunction, address, decode_tuples)


class ContractEvents(BaseContractEvents):
    def __init__(
        self, abi: ABI, w3: "Web3", address: Optional[ChecksumAddress] = None
    ) -> None:
        super().__init__(abi, w3, ContractEvent, address)


class ContractEvent(BaseContractEvent):
    @combomethod
    def get_logs(
        self,
        argument_filters: Optional[Dict[str, Any]] = None,
        fromBlock: Optional[BlockIdentifier] = None,
        toBlock: Optional[BlockIdentifier] = None,
        block_hash: Optional[HexBytes] = None,
    ) -> Iterable[EventData]:
        """Get events for this contract instance using eth_getLogs API.

        This is a stateless method, as opposed to create_filter.
        It can be safely called against nodes which do not provide
        eth_newFilter API, like Infura nodes.

        If there are many events,
        like ``Transfer`` events for a popular token,
        the Ethereum node might be overloaded and timeout
        on the underlying JSON-RPC call.

        Example - how to get all ERC-20 token transactions
        for the latest 10 blocks:

        .. code-block:: python

            from = max(mycontract.web3.eth.block_number - 10, 1)
            to = mycontract.web3.eth.block_number

            events = mycontract.events.Transfer.get_logs(fromBlock=from, toBlock=to)

            for e in events:
                print(e["args"]["from"],
                    e["args"]["to"],
                    e["args"]["value"])

        The returned processed log values will look like:

        .. code-block:: python

            (
                AttributeDict({
                 'args': AttributeDict({}),
                 'event': 'LogNoArguments',
                 'logIndex': 0,
                 'transactionIndex': 0,
                 'transactionHash': HexBytes('...'),
                 'address': '0xF2E246BB76DF876Cef8b38ae84130F4F55De395b',
                 'blockHash': HexBytes('...'),
                 'blockNumber': 3
                }),
                AttributeDict(...),
                ...
            )

        See also: :func:`web3.middleware.filter.local_filter_middleware`.

        :param argument_filters:
        :param fromBlock: block number or "latest", defaults to "latest"
        :param toBlock: block number or "latest". Defaults to "latest"
        :param block_hash: block hash. block_hash cannot be set at the
          same time as fromBlock or toBlock
        :yield: Tuple of :class:`AttributeDict` instances
        """
        abi = self._get_event_abi()
        # Call JSON-RPC API
        logs = self.w3.eth.get_logs(
            self._get_event_filter_params(
                abi, argument_filters, fromBlock, toBlock, block_hash
            )
        )

        # Convert raw binary data to Python proxy objects as described by ABI
        return tuple(get_event_data(self.w3.codec, abi, entry) for entry in logs)

    @combomethod
    def create_filter(
        self,
        *,  # PEP 3102
        argument_filters: Optional[Dict[str, Any]] = None,
        fromBlock: Optional[BlockIdentifier] = None,
        toBlock: BlockIdentifier = "latest",
        address: Optional[ChecksumAddress] = None,
        topics: Optional[Sequence[Any]] = None,
    ) -> LogFilter:
        """
        Create filter object that tracks logs emitted by this contract event.
        """
        filter_builder = EventFilterBuilder(self._get_event_abi(), self.w3.codec)
        self._set_up_filter_builder(
            argument_filters,
            fromBlock,
            toBlock,
            address,
            topics,
            filter_builder,
        )
        log_filter = filter_builder.deploy(self.w3)
        log_filter.log_entry_formatter = get_event_data(
            self.w3.codec, self._get_event_abi()
        )
        log_filter.builder = filter_builder

        return log_filter

    @combomethod
    def build_filter(self) -> EventFilterBuilder:
        builder = EventFilterBuilder(
            self._get_event_abi(),
            self.w3.codec,
            formatter=get_event_data(self.w3.codec, self._get_event_abi()),
        )
        builder.address = self.address
        return builder


class Contract(BaseContract):
    functions: ContractFunctions = None
    caller: "ContractCaller" = None

    # Instance of :class:`ContractEvents` presenting available Event ABIs
    events: ContractEvents = None

    def __init__(self, address: Optional[ChecksumAddress] = None) -> None:
        """Create a new smart contract proxy object.
        :param address: Contract address as 0x hex string"""
        _w3 = self.w3
        if _w3 is None:
            raise AttributeError(
                "The `Contract` class has not been initialized.  Please use the "
                "`web3.contract` interface to create your contract class."
            )

        if address:
            _ens = cast("ENS", _w3.ens)
            self.address = normalize_address(_ens, address)

        if not self.address:
            raise TypeError(
                "The address argument is required to instantiate a contract."
            )

        self.functions = ContractFunctions(
            self.abi, _w3, self.address, decode_tuples=self.decode_tuples
        )
        self.caller = ContractCaller(
            self.abi, _w3, self.address, decode_tuples=self.decode_tuples
        )
        self.events = ContractEvents(self.abi, _w3, self.address)
        self.fallback = Contract.get_fallback_function(
            self.abi,
            _w3,
            ContractFunction,
            self.address,
        )
        self.receive = Contract.get_receive_function(
            self.abi,
            _w3,
            ContractFunction,
            self.address,
        )

    @classmethod
    def factory(
        cls, w3: "Web3", class_name: Optional[str] = None, **kwargs: Any
    ) -> "Contract":
        kwargs["w3"] = w3

        normalizers = {
            "abi": normalize_abi,
            "address": partial(normalize_address, kwargs["w3"].ens),
            "bytecode": normalize_bytecode,
            "bytecode_runtime": normalize_bytecode,
        }

        contract = cast(
            Contract,
            PropertyCheckingFactory(
                class_name or cls.__name__,
                (cls,),
                kwargs,
                normalizers=normalizers,
            ),
        )
        contract.functions = ContractFunctions(
            contract.abi, contract.w3, decode_tuples=contract.decode_tuples
        )
        contract.caller = ContractCaller(
            contract.abi,
            contract.w3,
            contract.address,
            decode_tuples=contract.decode_tuples,
        )
        contract.events = ContractEvents(contract.abi, contract.w3)
        contract.fallback = Contract.get_fallback_function(
            contract.abi,
            contract.w3,
            ContractFunction,
        )
        contract.receive = Contract.get_receive_function(
            contract.abi,
            contract.w3,
            ContractFunction,
        )

        return contract

    @classmethod
    def constructor(cls, *args: Any, **kwargs: Any) -> "ContractConstructor":
        """
        :param args: The contract constructor arguments as positional arguments
        :param kwargs: The contract constructor arguments as keyword arguments
        :return: a contract constructor object
        """
        if cls.bytecode is None:
            raise ValueError(
                "Cannot call constructor on a contract that does not have "
                "'bytecode' associated with it"
            )

        return ContractConstructor(cls.w3, cls.abi, cls.bytecode, *args, **kwargs)

    @combomethod
    def find_functions_by_identifier(
        cls,
        contract_abi: ABI,
        w3: "Web3",
        address: ChecksumAddress,
        callable_check: Callable[..., Any],
    ) -> List["ContractFunction"]:
        return find_functions_by_identifier(  # type: ignore
            contract_abi, w3, address, callable_check, ContractFunction
        )

    @combomethod
    def get_function_by_identifier(
        cls, fns: Sequence[BaseContractFunction], identifier: str
    ) -> BaseContractFunction:
        return get_function_by_identifier(fns, identifier)


class ContractConstructor(BaseContractConstructor):
    @combomethod
    def transact(self, transaction: Optional[TxParams] = None) -> HexBytes:
        return self.w3.eth.send_transaction(self._get_transaction(transaction))

    @combomethod
    def build_transaction(self, transaction: Optional[TxParams] = None) -> TxParams:
        """
        Build the transaction dictionary without sending
        """
        built_transaction = self._build_transaction(transaction)
        return fill_transaction_defaults(self.w3, built_transaction)

    @combomethod
    def estimate_gas(
        self,
        transaction: Optional[TxParams] = None,
        block_identifier: Optional[BlockIdentifier] = None,
    ) -> int:
        transaction = self._estimate_gas(transaction)

        return self.w3.eth.estimate_gas(transaction, block_identifier=block_identifier)


class ContractFunction(BaseContractFunction):
    def __call__(self, *args: Any, **kwargs: Any) -> "ContractFunction":
        clone = copy.copy(self)
        if args is None:
            clone.args = tuple()
        else:
            clone.args = args

        if kwargs is None:
            clone.kwargs = {}
        else:
            clone.kwargs = kwargs
        clone._set_function_info()
        return clone

    def call(
        self,
        transaction: Optional[TxParams] = None,
        block_identifier: BlockIdentifier = "latest",
        state_override: Optional[CallOverride] = None,
        ccip_read_enabled: Optional[bool] = None,
    ) -> Any:
        """
        Execute a contract function call using the `eth_call` interface.

        This method prepares a ``Caller`` object that exposes the contract
        functions and public variables as callable Python functions.

        Reading a public ``owner`` address variable example:

        .. code-block:: python

            ContractFactory = w3.eth.contract(
                abi=wallet_contract_definition["abi"]
            )

            # Not a real contract address
            contract = ContractFactory("0x2f70d3d26829e412A602E83FE8EeBF80255AEeA5")

            # Read "owner" public variable
            addr = contract.functions.owner().call()

        :param transaction: Dictionary of transaction info for web3 interface
        :return: ``Caller`` object that has contract public functions
            and variables exposed as Python methods
        """
        call_transaction = self._get_call_txparams(transaction)

        block_id = parse_block_identifier(self.w3, block_identifier)

        return call_contract_function(
            self.w3,
            self.address,
            self._return_data_normalizers,
            self.function_identifier,
            call_transaction,
            block_id,
            self.contract_abi,
            self.abi,
            state_override,
            ccip_read_enabled,
            self.decode_tuples,
            *self.args,
            **self.kwargs,
        )

    def transact(self, transaction: Optional[TxParams] = None) -> HexBytes:
        setup_transaction = self._transact(transaction)
        return transact_with_contract_function(
            self.address,
            self.w3,
            self.function_identifier,
            setup_transaction,
            self.contract_abi,
            self.abi,
            *self.args,
            **self.kwargs,
        )

    def estimate_gas(
        self,
        transaction: Optional[TxParams] = None,
        block_identifier: Optional[BlockIdentifier] = None,
    ) -> int:
        setup_transaction = self._estimate_gas(transaction)
        return estimate_gas_for_function(
            self.address,
            self.w3,
            self.function_identifier,
            setup_transaction,
            self.contract_abi,
            self.abi,
            block_identifier,
            *self.args,
            **self.kwargs,
        )

    def build_transaction(self, transaction: Optional[TxParams] = None) -> TxParams:
        built_transaction = self._build_transaction(transaction)
        return build_transaction_for_function(
            self.address,
            self.w3,
            self.function_identifier,
            built_transaction,
            self.contract_abi,
            self.abi,
            *self.args,
            **self.kwargs,
        )


class ContractCaller(BaseContractCaller):
    def __init__(
        self,
        abi: ABI,
        w3: "Web3",
        address: ChecksumAddress,
        transaction: Optional[TxParams] = None,
        block_identifier: BlockIdentifier = "latest",
        ccip_read_enabled: Optional[bool] = None,
        decode_tuples: Optional[bool] = False,
    ) -> None:
        super().__init__(
            abi=abi,
            w3=w3,
            address=address,
            transaction=transaction,
            block_identifier=block_identifier,
            ccip_read_enabled=ccip_read_enabled,
            contract_function_class=ContractFunction,
            decode_tuples=decode_tuples,
        )

    def __call__(
        self,
        transaction: Optional[TxParams] = None,
        block_identifier: BlockIdentifier = "latest",
        ccip_read_enabled: Optional[bool] = None,
    ) -> "ContractCaller":
        if transaction is None:
            transaction = {}

        return type(self)(
            self.abi,
            self.w3,
            self.address,
            transaction=transaction,
            block_identifier=block_identifier,
            ccip_read_enabled=ccip_read_enabled,
            decode_tuples=self.decode_tuples,
        )
